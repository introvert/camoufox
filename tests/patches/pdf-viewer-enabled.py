"""
Verify pdf-viewer-spoofing.patch: `pdfViewerEnabled` in the config sets
navigator.pdfViewerEnabled, and navigator.plugins / navigator.mimeTypes follow
it, both to true and to false.

The HTML spec ties the three together. With a viewer, a browser exposes five
hard-coded plugins ("PDF Viewer", "Chrome PDF Viewer", ...) and two MIME types
(application/pdf, text/pdf); without one, both lists are empty. Gecko decides
all three from pdfjs.disabled, so before this patch the config key -- declared
in properties.json -- was never read at all, and overriding only the boolean
would have left five plugins beside pdfViewerEnabled == false.

Run from any venv that has playwright:
    python tests/patches/pdf-viewer-enabled.py
    python tests/patches/pdf-viewer-enabled.py --binary /path/to/camoufox-bin
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

WITH_VIEWER = {
    "navigator.pdfViewerEnabled": True,
    "navigator.plugins.length": 5,
    "navigator.mimeTypes.length": 2,
    "plugins[0].name": "PDF Viewer",
    "mimeTypes['application/pdf'].type": "application/pdf",
}

WITHOUT_VIEWER = {
    "navigator.pdfViewerEnabled": False,
    "navigator.plugins.length": 0,
    "navigator.mimeTypes.length": 0,
    "plugins[0].name": None,
    "mimeTypes['application/pdf'].type": None,
}

PROBE_JS = r"""() => ({
  "navigator.pdfViewerEnabled": navigator.pdfViewerEnabled,
  "navigator.plugins.length": navigator.plugins.length,
  "navigator.mimeTypes.length": navigator.mimeTypes.length,
  "plugins[0].name": navigator.plugins[0] ? navigator.plugins[0].name : null,
  "mimeTypes['application/pdf'].type":
      navigator.mimeTypes["application/pdf"] ? navigator.mimeTypes["application/pdf"].type : null,
})"""


def resolve_binary(argv) -> Optional[Path]:
    if "--binary" in argv:
        return Path(argv[argv.index("--binary") + 1]).resolve()
    if os.environ.get("CAMOUFOX_BINARY"):
        return Path(os.environ["CAMOUFOX_BINARY"]).resolve()
    matches = sorted(REPO_ROOT.glob("camoufox-*/obj-*/dist/bin/camoufox-bin"))
    return matches[-1] if matches else None


async def probe(binary: Path, config: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
    from playwright.async_api import async_playwright

    env = dict(os.environ)
    env["CAMOU_CONFIG_1"] = json.dumps(config)
    async with async_playwright() as p:
        browser = await p.firefox.launch(
            executable_path=str(binary), headless=True, env=env, firefox_user_prefs=prefs
        )
        try:
            page = await browser.new_page()
            await page.goto("about:blank")
            return await page.evaluate(PROBE_JS)
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
        detail = f"{str(got):<16}" if ok else f"{str(got):<16} (expected {want})"
        print(f"    [{'ok  ' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    return failures == 0


async def main() -> int:
    binary = resolve_binary(sys.argv)
    if binary is None or not binary.exists():
        print(f"FATAL: no camoufox binary found (looked for {binary})")
        return 1
    print(f"Binary: {binary}")

    # The config has to win over the pref in both directions, so each case
    # runs against the pref that would otherwise say the opposite.
    ok = True
    ok &= compare("config true, pdfjs.disabled = true",
                  await probe(binary, {"pdfViewerEnabled": True}, {"pdfjs.disabled": True}),
                  WITH_VIEWER)
    ok &= compare("config false, pdfjs.disabled = false",
                  await probe(binary, {"pdfViewerEnabled": False}, {"pdfjs.disabled": False}),
                  WITHOUT_VIEWER)
    ok &= compare("unset: follows pdfjs.disabled = false",
                  await probe(binary, {}, {"pdfjs.disabled": False}), WITH_VIEWER)
    ok &= compare("unset: follows pdfjs.disabled = true",
                  await probe(binary, {}, {"pdfjs.disabled": True}), WITHOUT_VIEWER)

    print("\nPASS" if ok else "\nFAIL: see the rows marked FAIL above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
