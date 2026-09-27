"""
Verify navigator-config-keys.patch and the navigator.languages half of
locale-spoofing.patch: navigator keys that properties.json declared but no
patch read now reach the page, on the window and in a worker alike, and an
unset key still gives Gecko's own value.

Run from any venv that has playwright:
    python tests/patches/navigator-config-keys.py
    python tests/patches/navigator-config-keys.py --binary /path/to/camoufox-bin
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

SET: Dict[str, Any] = {
    "navigator.appCodeName": "Mozilla2",
    "navigator.appName": "Netscape2",
    "navigator.product": "Gecko2",
    "navigator.productSub": "20200101",
    "navigator.vendor": "Camoufox Test",
    "navigator.buildID": "20240101000000",
    "navigator.cookieEnabled": False,
    "navigator.onLine": False,
    "navigator.languages": ["de-DE", "de", "en"],
}

# What Gecko reports on a page when nothing is set.
GECKO_DEFAULTS: Dict[str, Any] = {
    "navigator.appCodeName": "Mozilla",
    "navigator.appName": "Netscape",
    "navigator.product": "Gecko",
    "navigator.productSub": "20100101",
    "navigator.vendor": "",
    "navigator.buildID": "20181001000000",
    "navigator.cookieEnabled": True,
    "navigator.onLine": True,
}

# Workers expose only some of these.
WORKER_KEYS = ("navigator.appCodeName", "navigator.appName", "navigator.product",
               "navigator.onLine", "navigator.languages")

WINDOW_JS = r"""() => ({
  "navigator.appCodeName": navigator.appCodeName,
  "navigator.appName": navigator.appName,
  "navigator.product": navigator.product,
  "navigator.productSub": navigator.productSub,
  "navigator.vendor": navigator.vendor,
  "navigator.buildID": navigator.buildID,
  "navigator.cookieEnabled": navigator.cookieEnabled,
  "navigator.onLine": navigator.onLine,
  "navigator.languages": Array.from(navigator.languages),
})"""

WORKER_JS = r"""() => new Promise(resolve => {
  const src = `postMessage({
    "navigator.appCodeName": navigator.appCodeName,
    "navigator.appName": navigator.appName,
    "navigator.product": navigator.product,
    "navigator.onLine": navigator.onLine,
    "navigator.languages": Array.from(navigator.languages),
  })`;
  const w = new Worker(URL.createObjectURL(new Blob([src], {type: "text/javascript"})));
  w.onmessage = e => resolve(e.data);
})"""


def resolve_binary(argv) -> Optional[Path]:
    if "--binary" in argv:
        return Path(argv[argv.index("--binary") + 1]).resolve()
    if os.environ.get("CAMOUFOX_BINARY"):
        return Path(os.environ["CAMOUFOX_BINARY"]).resolve()
    matches = sorted(REPO_ROOT.glob("camoufox-*/obj-*/dist/bin/camoufox-bin"))
    return matches[-1] if matches else None


async def probe(binary: Path, config: Dict[str, Any]):
    from playwright.async_api import async_playwright

    env = dict(os.environ)
    env["CAMOU_CONFIG_1"] = json.dumps(config)
    async with async_playwright() as p:
        browser = await p.firefox.launch(executable_path=str(binary), headless=True, env=env)
        try:
            page = await browser.new_page()
            # A real https origin: cookieEnabled is false on an opaque data: one.
            await page.route("https://probe.test/**", lambda route: route.fulfill(
                status=200, content_type="text/html", body="<p>probe</p>"))
            await page.goto("https://probe.test/")
            return await page.evaluate(WINDOW_JS), await page.evaluate(WORKER_JS)
        finally:
            await browser.close()


def compare(title: str, actual: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    print(f"\n=== {title} ===")
    width = max(len(k) for k in expected)
    failures = 0
    for name, want in expected.items():
        got = actual.get(name)
        ok = got == want
        failures += not ok
        detail = f"{got!s:<22}" if ok else f"{got!s:<22} (expected {want})"
        print(f"    [{'ok  ' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    return failures == 0


async def main() -> int:
    binary = resolve_binary(sys.argv)
    if binary is None or not binary.exists():
        print(f"FATAL: no camoufox binary found (looked for {binary})")
        return 1
    print(f"Binary: {binary}")

    ok = True
    window, worker = await probe(binary, SET)
    ok &= compare("set: window", window, SET)
    ok &= compare("set: worker", worker, {k: SET[k] for k in WORKER_KEYS})

    window, worker = await probe(binary, {})
    ok &= compare("unset: window keeps Gecko's values", window, GECKO_DEFAULTS)
    ok &= compare("unset: worker matches the window", worker,
                  {k: window[k] for k in WORKER_KEYS})

    print("\nPASS" if ok else "\nFAIL: see the rows marked FAIL above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
