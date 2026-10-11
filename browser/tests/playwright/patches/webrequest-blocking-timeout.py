"""
Verify a blocking webRequest listener that never answers cannot park a request forever.

Gecko suspends the channel and awaits the promise a blocking listener returned,
with no deadline of its own. `nsHttpChannel::OnSuspendTimeout` does not rescue
it -- that only bypasses the cache writer lock -- so a listener that never
settles parks the navigation until the page is torn down. Seen in production
with uBlock Origin and six browser contexts started at once: the last contexts
never navigated at all, `goto` still pending after ninety seconds, while the
page answered `evaluate` and sat on `about:blank`. MOZ_LOG showed the suspend at
`WebRequest.sys.mjs`, the 5s suspend timer firing without resuming, and the
resume arriving only as the channel was cancelled.

`webrequest-blocking-timeout.patch` gives that wait a deadline
(`extensions.webRequest.blockingResponseTimeoutMs`, 10s by default, 0 to restore
Gecko's unbounded wait). A listener past the deadline is treated as having no
opinion, which is what a non-blocking listener is, and the request proceeds.

The extension here is built on the fly and answers nothing for one URL, so the
hang is deterministic rather than a race that needs load to appear.

Run against a specific build:
    CAMOUFOX_EXECUTABLE_PATH=/path/to/camoufox-bin python tests/patches/webrequest-blocking-timeout.py
(without the env var it uses the camoufox-managed browser download.)

What PASS means:
    * a request no listener holds is unaffected;
    * a request held by a listener that never answers is released at the
      deadline, and reaches the server;
    * and with the deadline off, that same request is still held -- which is
      what proves the extension is really blocking, and so that the two checks
      above are not passing vacuously.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox

EXECUTABLE_PATH = os.environ.get("CAMOUFOX_EXECUTABLE_PATH")

# Short enough to keep the test quick, long enough to sit well clear of the
# sub-second time a healthy request takes.
DEADLINE_MS = 3000
HELD_URL_TIMEOUT = 20_000

MANIFEST = {
    "manifest_version": 2,
    "name": "Blocking listener that never answers",
    "version": "1.0",
    "browser_specific_settings": {
        "gecko": {
            "id": "never-answers@camoufox.test",
            "data_collection_permissions": {"required": ["none"]},
            "strict_min_version": "115.0",
        }
    },
    "permissions": ["webRequest", "webRequestBlocking", "<all_urls>"],
    # A background page, not `scripts`: a temporarily installed addon declaring
    # `background.scripts` registers no listener at all here, which would make
    # this test pass while proving nothing.
    "background": {"page": "background.html"},
}

BACKGROUND_JS = """
browser.webRequest.onBeforeRequest.addListener(
  details => {
    if (details.url.includes("/held")) {
      return new Promise(() => {});
    }
    // A listener that is registered but not yet live would make every check
    // here vacuous, so give the test something to wait for.
    if (details.url.includes("/probe")) {
      return { redirectUrl: details.url.replace("/probe", "/listening") };
    }
    return {};
  },
  { urls: ["<all_urls>"] },
  ["blocking"]
);
"""


def _write_extension(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(MANIFEST))
    (directory / "background.html").write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<script src="bg.js"></script></head><body></body></html>'
    )
    (directory / "bg.js").write_text(BACKGROUND_JS)
    return str(directory)


class Origin:
    """A server that answers everything, so a missing request means the browser never sent one."""

    def __init__(self) -> None:
        self.seen: List[str] = []

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    def close(self) -> None:
        self._server.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                self.seen.append(head.decode(errors="replace").split("\r\n")[0])
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


async def _wait_until_listening(page, port: int, origin: "Origin", attempts: int = 40) -> bool:
    """A temporarily installed addon's background page takes a moment to start."""
    for _ in range(attempts):
        try:
            await page.goto(f"http://127.0.0.1:{port}/probe", timeout=5000)
        except Exception:
            pass
        if any("/listening" in line for line in origin.seen):
            return True
        await asyncio.sleep(0.25)
    return False


async def _visit(extension: str, deadline_ms: Optional[int]) -> Tuple[Dict[str, float], List[str]]:
    origin = Origin()
    port = await origin.start()
    timings: Dict[str, float] = {}
    prefs = (
        {}
        if deadline_ms is None
        else {"firefox_user_prefs": {"extensions.webRequest.blockingResponseTimeoutMs": deadline_ms}}
    )
    try:
        async with AsyncCamoufox(
            headless=True,
            executable_path=EXECUTABLE_PATH,
            addons=[extension],
            # uBlock Origin is a blocking listener of its own; leave it out so
            # only the listener under test can hold a request.
            exclude_addons=[DefaultAddons.UBO],
            **prefs,
        ) as browser:
            page = await browser.new_page()
            if not await _wait_until_listening(page, port, origin):
                raise RuntimeError(
                    "the test extension's blocking listener never became live; "
                    "without it this test proves nothing"
                )
            for path in ("free", "held"):
                started = time.monotonic()
                try:
                    await page.goto(f"http://127.0.0.1:{port}/{path}", timeout=HELD_URL_TIMEOUT)
                    timings[path] = time.monotonic() - started
                except Exception:
                    timings[path] = float("inf")
    finally:
        origin.close()
    return timings, origin.seen


def _check(results: Dict[str, bool], label: str, ok: bool, detail: str) -> None:
    results[label] = ok
    print(f"  {'PASS' if ok else 'FAIL'} {label:44} -> {detail}")


async def _run() -> bool:
    results: Dict[str, bool] = {}
    with tempfile.TemporaryDirectory() as tmp:
        extension = _write_extension(Path(tmp) / "never-answers")

        print(f"-- deadline {DEADLINE_MS}ms --")
        timings, seen = await _visit(extension, DEADLINE_MS)
        held = timings["held"]
        _check(
            results,
            "request no listener holds is unaffected",
            timings["free"] < 2.0,
            f"{timings['free']:.1f}s",
        )
        _check(
            results,
            "held request is released at the deadline",
            DEADLINE_MS / 1000 <= held < DEADLINE_MS / 1000 + 5,
            "never released" if held == float("inf") else f"{held:.1f}s",
        )
        _check(
            results,
            "released request reaches the server",
            any("/held" in line for line in seen),
            ", ".join(seen) or "nothing served",
        )

        print("-- deadline off (pref 0), the same request must still be held --")
        timings, seen = await _visit(extension, 0)
        _check(
            results,
            "listener really blocks, so the checks above mean something",
            timings["held"] == float("inf") and not any("/held" in line for line in seen),
            "held until the navigation timed out"
            if timings["held"] == float("inf")
            else f"released after {timings['held']:.1f}s",
        )

    return all(results.values())


async def main() -> int:
    passed = await _run()
    print()
    if passed:
        print("PASS: a listener that never answers no longer parks the request")
        return 0
    print("FAIL: the blocking-listener deadline is not holding")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
