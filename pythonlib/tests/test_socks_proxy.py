"""
Authenticated SOCKS5 support: proxy parsing, the local relay, and the geo lookup.

Everything runs against an in-process SOCKS5 server that requires a username and
password and sends every tunnel to a local HTTP server, so no network is needed.
"""

import asyncio
import json
import socket
import threading
from typing import Dict, List, Tuple

import pytest
import requests

from camoufox.exceptions import InvalidProxy
from camoufox.ip import resolve_proxy_geo
from camoufox.socks import (
    _manager,
    needs_relay,
    normalize_proxy,
    prepare_proxy,
    requests_proxy_url,
)


class Upstream:
    """Username/password SOCKS5 server on a background loop; records (user, host) per tunnel."""

    def __init__(self, credentials: Dict[str, str], target_port: int) -> None:
        self.credentials = credentials
        self.target_port = target_port
        self.log: List[Tuple[str, str]] = []
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        server = asyncio.run_coroutine_threadsafe(
            asyncio.start_server(self._handle, '127.0.0.1', 0), self.loop
        ).result()
        self.port = server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            _, n = await reader.readexactly(2)
            if 0x02 not in await reader.readexactly(n):
                writer.write(b'\x05\xff')
                return
            writer.write(b'\x05\x02')
            _, ulen = await reader.readexactly(2)
            user = (await reader.readexactly(ulen)).decode()
            plen = (await reader.readexactly(1))[0]
            pwd = (await reader.readexactly(plen)).decode()
            if self.credentials.get(user) != pwd:
                writer.write(b'\x01\x01')
                return
            writer.write(b'\x01\x00')
            _, _, _, atyp = await reader.readexactly(4)
            assert atyp == 0x03, "relay should forward the hostname, not an address"
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
            await reader.readexactly(2)
            self.log.append((user, host))
            up_r, up_w = await asyncio.open_connection('127.0.0.1', self.target_port)
            writer.write(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')

            async def pipe(r, w):
                try:
                    while data := await r.read(65536):
                        w.write(data)
                        await w.drain()
                except OSError:
                    pass
                finally:
                    w.close()

            await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@pytest.fixture(scope='module')
def http_port():
    """HTTP server answering with ip-api.com style JSON."""
    body = json.dumps({'query': '203.0.113.7', 'timezone': 'Europe/Ljubljana'}).encode()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(16)

    def serve():
        while True:
            conn, _ = listener.accept()
            with conn:
                data = b''
                while b'\r\n\r\n' not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                conn.sendall(
                    b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n'
                    + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body
                )

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1]


@pytest.fixture(scope='module')
def upstream(http_port):
    return Upstream({'alice': 'p@ss:w/rd', 'bob': 'hunter2'}, http_port)


def test_needs_relay_only_for_authenticated_socks5():
    assert needs_relay({'server': 'socks5://h:1080', 'username': 'u', 'password': 'p'})
    assert needs_relay({'server': 'socks5h://u:p@h:1080'})
    assert not needs_relay({'server': 'socks5://h:1080'})
    assert not needs_relay({'server': 'http://h:8080', 'username': 'u', 'password': 'p'})
    assert not needs_relay({'server': 'h:8080'})
    assert not needs_relay(None)


def test_normalize_moves_url_credentials_and_socks5h():
    assert normalize_proxy({'server': 'socks5h://al%40ce:p%3Aw@proxy.example:9050'}) == {
        'server': 'socks5://proxy.example:9050',
        'username': 'al@ce',
        'password': 'p:w',
    }
    # Explicit keys win over URL credentials, and the default port is filled in.
    assert normalize_proxy({'server': 'socks5://x:y@[::1]', 'username': 'u', 'password': 'p'}) == {
        'server': 'socks5://[::1]:1080',
        'username': 'u',
        'password': 'p',
    }
    http = {'server': 'http://h:1', 'username': 'u'}
    assert normalize_proxy(http) is http


def test_requests_proxy_url_encodes_credentials():
    assert (
        requests_proxy_url({'server': 'socks5://h:1080', 'username': 'a@b', 'password': 'c:d/e'})
        == 'socks5h://a%40b:c%3Ad%2Fe@h:1080'
    )
    assert requests_proxy_url({'server': 'h:3128'}) == 'http://h:3128'
    with pytest.raises(InvalidProxy):
        requests_proxy_url({'server': 'socks5://:1080'})


def test_unauthenticated_socks5_is_passed_through():
    proxy, release = prepare_proxy({'server': 'socks5://h:1080', 'bypass': 'localhost'})
    assert proxy == {'server': 'socks5://h:1080', 'bypass': 'localhost'}
    release()


def test_relay_tunnels_through_authenticated_upstream(upstream):
    proxy, release = prepare_proxy(
        {'server': f'socks5://127.0.0.1:{upstream.port}', 'username': 'alice', 'password': 'p@ss:w/rd',
         'bypass': 'localhost'}
    )
    try:
        assert proxy['server'].startswith('socks5://127.0.0.1:')
        assert 'username' not in proxy and 'password' not in proxy
        assert proxy['bypass'] == 'localhost'
        # What the browser does: plain SOCKS5 to the relay, hostname unresolved.
        relay_url = proxy['server'].replace('socks5://', 'socks5h://')
        resp = requests.get(
            'http://unresolvable.camoufox.test/',
            proxies={'http': relay_url, 'https': relay_url},
            timeout=10,
        )
        assert resp.json()['query'] == '203.0.113.7'
        assert upstream.log[-1] == ('alice', 'unresolvable.camoufox.test')
    finally:
        release()


def test_relay_is_shared_and_reference_counted(upstream):
    before = _manager.active()
    cfg = {'server': f'socks5://127.0.0.1:{upstream.port}', 'username': 'bob', 'password': 'hunter2'}
    a, release_a = prepare_proxy(cfg)
    b, release_b = prepare_proxy(cfg)
    c, release_c = prepare_proxy({**cfg, 'username': 'alice', 'password': 'p@ss:w/rd'})
    assert a == b and a != c
    assert _manager.active() == before + 2
    release_a()
    release_a()  # releasing twice must not drop the other holder's reference
    assert _manager.active() == before + 2
    release_b()
    release_c()
    assert _manager.active() == before
    port = int(a['server'].rsplit(':', 1)[1])
    with pytest.raises(OSError):
        socket.create_connection(('127.0.0.1', port), timeout=2).close()


def test_relay_reports_bad_credentials(upstream):
    proxy, release = prepare_proxy(
        {'server': f'socks5://127.0.0.1:{upstream.port}', 'username': 'alice', 'password': 'wrong'}
    )
    try:
        relay_url = proxy['server'].replace('socks5://', 'socks5h://')
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get('http://x.camoufox.test/', proxies={'http': relay_url}, timeout=10)
    finally:
        release()


def test_relay_reports_unreachable_upstream():
    dead = socket.socket()
    dead.bind(('127.0.0.1', 0))
    port = dead.getsockname()[1]
    dead.close()
    proxy, release = prepare_proxy({'server': f'socks5://127.0.0.1:{port}', 'username': 'u', 'password': 'p'})
    try:
        relay_url = proxy['server'].replace('socks5://', 'socks5h://')
        with pytest.raises(requests.exceptions.ConnectionError):
            requests.get('http://x.camoufox.test/', proxies={'http': relay_url}, timeout=10)
    finally:
        release()


def test_geo_lookup_goes_through_authenticated_socks5(upstream):
    geo = resolve_proxy_geo(
        {'server': f'socks5://127.0.0.1:{upstream.port}', 'username': 'bob', 'password': 'hunter2'}
    )
    assert geo == {'ip': '203.0.113.7', 'timezone': 'Europe/Ljubljana'}
    assert upstream.log[-1] == ('bob', 'ip-api.com')
