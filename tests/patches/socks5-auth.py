"""
Verify authenticated SOCKS5 proxies work, launch-level and per context.

Playwright rejects a SOCKS5 proxy with a username or password ("Browser does not
support socks5 proxy authentication") before anything reaches the browser, so
the Python package routes such proxies through a local relay (camoufox/socks.py)
that performs the RFC 1929 handshake with the real proxy.

The SOCKS5 proxy here is in-process: it requires username/password auth, records
who logged in and which destination they asked for, and connects every CONNECT to
a local HTTP server that echoes the username back. Destinations are fake hostnames
(`*.camoufox.test`) the local machine cannot resolve, so a page only loads if the
hostname reached the proxy unresolved -- i.e. DNS goes through the proxy.

Run against a specific build:
    CAMOUFOX_EXECUTABLE_PATH=/path/to/camoufox-bin python tests/patches/socks5-auth.py
(without the env var it uses the camoufox-managed browser download.)

What PASS means:
    * a launch-level socks5 proxy with credentials loads pages, in both the
      `username`/`password` form and the `socks5://user:pass@host:port` form;
    * contexts sharing one proxy host with different usernames each reach the
      proxy as themselves, concurrently;
    * a wrong password never loads a page;
    * the proxy only ever sees hostnames, never pre-resolved addresses.
"""

import asyncio
import os
import sys
from typing import Dict, List, Tuple

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox, AsyncNewContext

EXECUTABLE_PATH = os.environ.get("CAMOUFOX_EXECUTABLE_PATH")
TIMEOUT = 20_000


class AuthSocks5Server:
    """A SOCKS5 server that requires username/password and routes everything to one local port."""

    def __init__(self, credentials: Dict[str, str], target_port: int) -> None:
        self.credentials = credentials
        self.target_port = target_port
        self.port = 0
        self.log: List[Tuple[str, str]] = []  # (username, destination host)
        self.rejected: List[str] = []

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            _, n = await reader.readexactly(2)
            if 0x02 not in await reader.readexactly(n):
                writer.write(b"\x05\xff")
                return
            writer.write(b"\x05\x02")
            _, ulen = await reader.readexactly(2)
            user = (await reader.readexactly(ulen)).decode()
            plen = (await reader.readexactly(1))[0]
            password = (await reader.readexactly(plen)).decode()
            if self.credentials.get(user) != password:
                self.rejected.append(user)
                writer.write(b"\x01\x01")
                return
            writer.write(b"\x01\x00")

            _, cmd, _, atyp = await reader.readexactly(4)
            if atyp == 0x03:
                host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
            elif atyp == 0x01:
                host = ".".join(str(b) for b in await reader.readexactly(4))
            else:
                host = (await reader.readexactly(16)).hex()
            await reader.readexactly(2)
            self.log.append((user, host))

            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.target_port)
            writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
            await writer.drain()
            # Tell the echo server who this tunnel belongs to.
            up_writer.write(f"{user}\n".encode())

            async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
                try:
                    while data := await r.read(65536):
                        w.write(data)
                        await w.drain()
                except OSError:
                    pass
                finally:
                    w.close()

            await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


async def echo_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Answers every HTTP request with the tunnel's username and the Host header."""
    try:
        user = (await reader.readline()).decode().strip()
        while True:
            request = await reader.readuntil(b"\r\n\r\n")
            host = next(
                (line.split(b":", 1)[1].strip().decode() for line in request.split(b"\r\n")
                 if line.lower().startswith(b"host:")),
                "",
            )
            body = f"<html><title>{user}@{host}</title></html>".encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


def launch_kwargs(**kwargs):
    if EXECUTABLE_PATH:
        kwargs["executable_path"] = EXECUTABLE_PATH
        # Otherwise the version is read from the managed install, which may not exist.
        kwargs.setdefault("ff_version", os.environ.get("CAMOUFOX_FF_VERSION", "155"))
    # Keep an addon's startup timing out of a proxy measurement.
    return {"headless": True, "i_know_what_im_doing": True, "exclude_addons": [DefaultAddons.UBO], **kwargs}


async def title_of(page, url: str) -> str:
    try:
        await page.goto(url, timeout=TIMEOUT)
        return await page.title()
    except Exception as e:
        return f"ERROR: {str(e).splitlines()[0]}"


async def main() -> int:
    http = await asyncio.start_server(echo_http, "127.0.0.1", 0)
    http_port = http.sockets[0].getsockname()[1]
    users = {f"user{i}": f"p@ss:{i}/word" for i in range(4)}
    socks = AuthSocks5Server(users, http_port)
    await socks.start()
    server = f"socks5://127.0.0.1:{socks.port}"
    failures: List[str] = []

    def check(name: str, got: str, want: str) -> None:
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (want {want!r})"))
        if not ok:
            failures.append(name)

    print("launch-level proxy, username/password keys")
    async with AsyncCamoufox(
        **launch_kwargs(proxy={"server": server, "username": "user0", "password": users["user0"]})
    ) as browser:
        page = await browser.new_page()
        check("page", await title_of(page, "http://launch.camoufox.test/"), "user0@launch.camoufox.test")
        check("second page", await title_of(await browser.new_page(), "http://two.camoufox.test/"),
              "user0@two.camoufox.test")

    print("launch-level proxy, credentials in the URL (socks5h://)")
    from urllib.parse import quote
    embedded = f"socks5h://user1:{quote(users['user1'], safe='')}@127.0.0.1:{socks.port}"
    async with AsyncCamoufox(**launch_kwargs(proxy={"server": embedded})) as browser:
        page = await browser.new_page()
        check("page", await title_of(page, "http://url.camoufox.test/"), "user1@url.camoufox.test")

    print("per-context proxies, one host, different usernames, concurrently")
    async with AsyncCamoufox(**launch_kwargs()) as browser:
        contexts = [
            await AsyncNewContext(
                browser, proxy={"server": server, "username": u, "password": p}, webrtc_ip="1.2.3.4",
                timezone_id="UTC",
            )
            for u, p in users.items()
        ]

        async def load(ctx, user: str) -> Tuple[str, str]:
            page = await ctx.new_page()
            return user, await title_of(page, f"http://{user}.camoufox.test/")

        for _ in range(2):
            for user, got in await asyncio.gather(*(load(c, u) for c, u in zip(contexts, users))):
                check(f"context {user}", got, f"{user}@{user}.camoufox.test")

        print("wrong password")
        bad = await AsyncNewContext(
            browser, proxy={"server": server, "username": "user0", "password": "nope"},
            webrtc_ip="1.2.3.4", timezone_id="UTC",
        )
        got = await title_of(await bad.new_page(), "http://bad.camoufox.test/")
        print(f"  {'PASS' if got.startswith('ERROR') else 'FAIL'} refused: {got!r}")
        if not got.startswith("ERROR"):
            failures.append("wrong password")
        for ctx in contexts + [bad]:
            await ctx.close()

    from camoufox.socks import _manager
    # Launch-level relays live for the process; context relays close with their context.
    check("relays left after contexts closed", str(_manager.active()), "2")

    resolved = [host for _, host in socks.log if not host.endswith(".camoufox.test")]
    check("proxy only saw hostnames", repr(resolved), "[]")

    http.close()
    print("\nPASS" if not failures else f"\nFAIL: {', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
