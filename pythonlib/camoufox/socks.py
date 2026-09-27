"""
Authenticated SOCKS5 proxy support.

Playwright refuses a `socks5://` proxy that carries a username or password
("Browser does not support socks5 proxy authentication") before the request
ever reaches the browser. To support them anyway, Camoufox starts a relay on
the loopback interface: the browser talks plain (no-auth) SOCKS5 to the relay,
and the relay opens every connection through the real upstream proxy, doing
the RFC 1929 username/password handshake itself.

The relay forwards the destination exactly as the browser sent it. Firefox
sends hostnames, not resolved addresses, to a SOCKS5 proxy, so DNS is still
resolved by the upstream proxy and nothing leaks from the local machine.

The relay only binds 127.0.0.1 and only accepts the CONNECT command, which is
all Firefox uses. Relays are shared per upstream (scheme, host, port, username,
password) and reference counted, so contexts that use the same proxy share one
listener and it closes once the last of them is released.
"""

import asyncio
import socket
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

from .exceptions import InvalidProxy

SOCKS5_SCHEMES = ('socks5', 'socks5h')

CONNECT_TIMEOUT = 30.0
HANDSHAKE_TIMEOUT = 30.0
BUFFER_SIZE = 64 * 1024

# SOCKS5 reply codes
REP_SUCCEEDED = 0x00
REP_GENERAL_FAILURE = 0x01
REP_NOT_ALLOWED = 0x02
REP_HOST_UNREACHABLE = 0x04
REP_CONNECTION_REFUSED = 0x05
REP_COMMAND_NOT_SUPPORTED = 0x07
REP_ADDRESS_NOT_SUPPORTED = 0x08


class _Abort(Exception):
    """The client broke the protocol; drop the connection without a reply."""


class SocksError(Exception):
    def __init__(self, message: str, reply: int = REP_GENERAL_FAILURE) -> None:
        super().__init__(message)
        self.reply = reply


@dataclass(frozen=True)
class Upstream:
    host: str
    port: int
    username: str
    password: str


def _parse_server(server: str) -> Tuple[str, str, Optional[int], str, str]:
    """
    Returns (scheme, host, port, url_username, url_password) for a proxy server string.
    """
    if '://' not in server:
        server = 'http://' + server
    parsed = urlparse(server)
    try:
        port = parsed.port
    except ValueError as e:
        raise InvalidProxy(f"Invalid proxy port in {server!r}") from e
    return (
        parsed.scheme.lower(),
        parsed.hostname or '',
        port,
        unquote(parsed.username or ''),
        unquote(parsed.password or ''),
    )


def needs_relay(proxy: Optional[Dict[str, str]]) -> bool:
    """
    True for a SOCKS5 proxy with credentials, which Playwright cannot pass to the browser.
    """
    if not proxy or not proxy.get('server'):
        return False
    scheme, _, _, url_user, url_pass = _parse_server(proxy['server'])
    if scheme not in SOCKS5_SCHEMES:
        return False
    return bool(proxy.get('username') or proxy.get('password') or url_user or url_pass)


def normalize_proxy(proxy: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    Accepts the forms users commonly pass and returns a Playwright-compatible dict:

    - `socks5h://` (the requests/curl spelling for remote DNS) becomes `socks5://`.
      Firefox always resolves through a SOCKS5 proxy, so the two are the same here.
    - Credentials embedded in the server URL (`socks5://user:pass@host:port`) are
      moved to the `username` / `password` keys, which Playwright expects.
    """
    if not proxy or not proxy.get('server'):
        return proxy
    scheme, host, port, url_user, url_pass = _parse_server(proxy['server'])
    if scheme not in SOCKS5_SCHEMES:
        return proxy
    if not host:
        raise InvalidProxy(f"Invalid proxy server: {proxy['server']}")
    result = dict(proxy)
    netloc = f'[{host}]' if ':' in host else host
    result['server'] = f"socks5://{netloc}:{port or 1080}"
    if url_user and not result.get('username'):
        result['username'] = url_user
    if url_pass and not result.get('password'):
        result['password'] = url_pass
    return result


def upstream_from_proxy(proxy: Dict[str, str]) -> Upstream:
    proxy = normalize_proxy(proxy) or {}
    _, host, port, _, _ = _parse_server(proxy['server'])
    username = proxy.get('username') or ''
    password = proxy.get('password') or ''
    # RFC 1929 length fields are one byte each.
    if len(username.encode()) > 255 or len(password.encode()) > 255:
        raise InvalidProxy("SOCKS5 username and password must each be at most 255 bytes")
    return Upstream(host=host, port=port or 1080, username=username, password=password)


async def _read_address(reader: asyncio.StreamReader, atyp: int) -> bytes:
    """
    Reads a SOCKS5 address + port (after ATYP) and returns the raw bytes.
    """
    if atyp == 0x01:
        return await reader.readexactly(4 + 2)
    if atyp == 0x04:
        return await reader.readexactly(16 + 2)
    if atyp == 0x03:
        length = await reader.readexactly(1)
        return length + await reader.readexactly(length[0] + 2)
    raise SocksError(f"Unsupported address type {atyp}", REP_ADDRESS_NOT_SUPPORTED)


def _reply(code: int) -> bytes:
    return bytes([0x05, code, 0x00, 0x01, 0, 0, 0, 0, 0, 0])


async def _upstream_connect(
    upstream: Upstream, atyp: int, address: bytes
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    """
    Opens a tunnel through the upstream proxy. Returns the stream pair and the
    upstream's full CONNECT reply, to be relayed to the browser verbatim.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(upstream.host, upstream.port), CONNECT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as e:
        raise SocksError(f"Cannot reach SOCKS5 proxy {upstream.host}:{upstream.port}: {e}") from e

    try:
        sock = writer.get_extra_info('socket')
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Offer only username/password when credentials are set, so a proxy
        # can never silently downgrade us to an unauthenticated session.
        method = 0x02 if (upstream.username or upstream.password) else 0x00
        writer.write(bytes([0x05, 0x01, method]))
        await writer.drain()
        ver, chosen = await reader.readexactly(2)
        if ver != 0x05:
            raise SocksError("Upstream proxy is not a SOCKS5 server")
        if chosen != method:
            raise SocksError(
                "Upstream SOCKS5 proxy rejected the authentication method", REP_NOT_ALLOWED
            )

        if method == 0x02:
            user = upstream.username.encode()
            pwd = upstream.password.encode()
            writer.write(bytes([0x01, len(user)]) + user + bytes([len(pwd)]) + pwd)
            await writer.drain()
            _, status = await reader.readexactly(2)
            if status != 0x00:
                raise SocksError(
                    "Upstream SOCKS5 proxy rejected the username/password", REP_NOT_ALLOWED
                )

        writer.write(bytes([0x05, 0x01, 0x00, atyp]) + address)
        await writer.drain()
        header = await reader.readexactly(4)
        if header[0] != 0x05:
            raise SocksError("Malformed reply from upstream SOCKS5 proxy")
        bound = await _read_address(reader, header[3])
        if header[1] != REP_SUCCEEDED:
            raise SocksError(f"Upstream SOCKS5 proxy refused the connection ({header[1]})", header[1])
        return reader, writer, header + bound
    except BaseException:
        writer.close()
        raise


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        # Half-close so the other direction can finish sending.
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except OSError:
            pass


class SocksRelay:
    """
    A no-auth SOCKS5 listener on 127.0.0.1 that tunnels through an authenticated upstream.
    """

    def __init__(self, upstream: Upstream, loop: asyncio.AbstractEventLoop) -> None:
        self.upstream = upstream
        self._loop = loop
        self._server: Optional[asyncio.AbstractServer] = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, '127.0.0.1', 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def server(self) -> str:
        return f"socks5://127.0.0.1:{self.port}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer = None
        try:
            try:
                atyp, address = await asyncio.wait_for(
                    self._client_handshake(reader, writer), HANDSHAKE_TIMEOUT
                )
                up_reader, upstream_writer, reply = await _upstream_connect(
                    self.upstream, atyp, address
                )
            except SocksError as e:
                writer.write(_reply(e.reply))
                await writer.drain()
                return
            writer.write(reply)
            await writer.drain()
            await asyncio.gather(_pipe(reader, upstream_writer), _pipe(up_reader, writer))
        except (_Abort, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        finally:
            for w in (writer, upstream_writer):
                if w is not None:
                    w.close()

    @staticmethod
    async def _client_handshake(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> Tuple[int, bytes]:
        ver, nmethods = await reader.readexactly(2)
        if ver != 0x05:
            raise _Abort()
        methods = await reader.readexactly(nmethods)
        if 0x00 not in methods:
            writer.write(b'\x05\xff')
            await writer.drain()
            raise _Abort()
        writer.write(b'\x05\x00')
        await writer.drain()

        ver, cmd, _, atyp = await reader.readexactly(4)
        address = await _read_address(reader, atyp)
        if cmd != 0x01:
            raise SocksError(f"Unsupported SOCKS command {cmd}", REP_COMMAND_NOT_SUPPORTED)
        return atyp, address


class _RelayManager:
    """
    Runs every relay on one background event loop thread and reference counts them.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._relays: Dict[Upstream, Tuple[SocksRelay, int]] = {}

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name='camoufox-socks-relay', daemon=True
            )
            thread.start()
            self._loop = loop
        return self._loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()).result()

    def acquire(self, upstream: Upstream) -> SocksRelay:
        with self._lock:
            entry = self._relays.get(upstream)
            if entry:
                relay, refs = entry
                self._relays[upstream] = (relay, refs + 1)
                return relay
            relay = SocksRelay(upstream, self._ensure_loop())
            self._run(relay.start())
            self._relays[upstream] = (relay, 1)
            return relay

    def release(self, upstream: Upstream) -> None:
        with self._lock:
            entry = self._relays.get(upstream)
            if not entry:
                return
            relay, refs = entry
            if refs > 1:
                self._relays[upstream] = (relay, refs - 1)
                return
            del self._relays[upstream]
            self._run(relay.stop())

    def active(self) -> int:
        with self._lock:
            return len(self._relays)


_manager = _RelayManager()


def prepare_proxy(
    proxy: Optional[Dict[str, str]],
) -> Tuple[Optional[Dict[str, str]], Callable[[], None]]:
    """
    Returns a proxy dict Playwright accepts, plus a function that releases any relay
    started for it. For an authenticated SOCKS5 proxy the result points at a local
    relay; every other proxy is returned (normalized) unchanged with a no-op release.
    """
    proxy = normalize_proxy(proxy)
    if not needs_relay(proxy):
        return proxy, lambda: None

    upstream = upstream_from_proxy(proxy)  # type: ignore[arg-type]
    relay = _manager.acquire(upstream)
    released = False

    def release() -> None:
        nonlocal released
        if not released:
            released = True
            _manager.release(upstream)

    local = {'server': relay.server}
    if proxy.get('bypass'):  # type: ignore[union-attr]
        local['bypass'] = proxy['bypass']  # type: ignore[index]
    return local, release


def requests_proxy_url(proxy: Dict[str, str]) -> str:
    """
    Builds a proxy URL for `requests`, with credentials percent-encoded. SOCKS5
    becomes `socks5h` so the lookup resolves DNS through the proxy like the browser does.
    """
    proxy = normalize_proxy(proxy) or {}
    scheme, host, port, url_user, url_pass = _parse_server(proxy.get('server', ''))
    if not host:
        raise InvalidProxy(f"Invalid proxy server: {proxy.get('server')}")
    if scheme in SOCKS5_SCHEMES:
        scheme = 'socks5h'
    user = proxy.get('username') or url_user
    pwd = proxy.get('password') or url_pass
    netloc = f'[{host}]' if ':' in host else host
    if port:
        netloc += f':{port}'
    if user or pwd:
        creds = quote(user or '', safe='')
        if pwd:
            creds += ':' + quote(pwd, safe='')
        netloc = f'{creds}@{netloc}'
    return f'{scheme}://{netloc}'
