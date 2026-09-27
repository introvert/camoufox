"""
Verify mobile mode (the `mobile` config key, and Playwright's isMobile/hasTouch
per context): force-default-pointer.patch, touchscreen-fingerprint-spoofing.patch,
mobile-meta-viewport.patch and the Juggler side in TargetRegistry.js.

A profile that claims Firefox for Android in its user agent gets a mobile page
from the server, and then that page's scripts ask the browser what it is. Real
Firefox for Android answers:

    (pointer: coarse), (hover: none)     its only pointer is the touchscreen
    'ontouchstart' in window             legacy touch APIs are on, on Android only
    navigator.maxTouchPoints == 5        a digitizer
    <meta name="viewport"> obeyed        the layout viewport follows the page

A desktop build with only the UA swapped answers every one of those like a
desktop, which is why Google served Camoufox a different layout than Firefox
for Android. These values are Gecko's own Android defaults: the ANDROID branch
of GetPointerCapabilities, dom.w3c_touch_events.legacy_apis.enabled (true only
on Android), dom.meta-viewport.enabled (true only on Android).

Three launches:

    mobile          CAMOU_CONFIG {"mobile": true}   -> every page is the phone
    per-context     a desktop launch, one context with isMobile + hasTouch
                    -> that context is the phone, a plain one beside it is not
    control         a desktop launch -> nothing moved

The meta viewport needs APZ on the widget, like any MobileViewportManager, so
the viewport checks run headful (under Xvfb on a machine without a display).

Run from any venv that has playwright:
    python tests/patches/mobile-mode.py
    python tests/patches/mobile-mode.py --binary /path/to/camoufox-bin

Which binary is tested, in order of precedence:
    --binary <path> | $CAMOUFOX_BINARY | the in-tree obj-*/dist/bin/camoufox-bin
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]

ANDROID_UA = "Mozilla/5.0 (Android 16; Mobile; rv:155.0) Gecko/155.0 Firefox/155.0"

PHONE: Dict[str, Any] = {
    "navigator.maxTouchPoints": 5,
    "'ontouchstart' in window": True,
    "'ontouchstart' in document": True,
    "'ontouchstart' in documentElement": True,
    "window.TouchEvent": True,
    "window.Touch": True,
    "window.TouchList": True,
    "document.createTouch": True,
    "document.createEvent('TouchEvent')": True,
    "(pointer: coarse)": True,
    "(pointer: fine)": False,
    "(any-pointer: coarse)": True,
    "(any-pointer: fine)": False,
    "(hover: none)": True,
    "(hover: hover)": False,
    "(any-hover: none)": True,
    "(any-hover: hover)": False,
    # Juggler's orientation override, from the phone's taller-than-wide screen.
    "screen.orientation.type": "portrait-primary",
    "screen.orientation.angle": 0,
}

DESKTOP: Dict[str, Any] = {
    "navigator.maxTouchPoints": 0,
    "'ontouchstart' in window": False,
    "window.TouchEvent": False,
    "document.createTouch": False,
    "(pointer: fine)": True,
    "(pointer: coarse)": False,
    "(any-pointer: coarse)": False,
    "(hover: hover)": True,
    "(any-hover: hover)": True,
}

PROBE_JS = r"""() => {
  const mq = q => window.matchMedia(q).matches;
  let createEvent = false;
  try { createEvent = !!document.createEvent('TouchEvent'); } catch (e) { createEvent = false; }
  return {
    "(pointer: fine)":       mq("(pointer: fine)"),
    "(pointer: coarse)":     mq("(pointer: coarse)"),
    "(any-pointer: fine)":   mq("(any-pointer: fine)"),
    "(any-pointer: coarse)": mq("(any-pointer: coarse)"),
    "(hover: hover)":        mq("(hover: hover)"),
    "(hover: none)":         mq("(hover: none)"),
    "(any-hover: hover)":    mq("(any-hover: hover)"),
    "(any-hover: none)":     mq("(any-hover: none)"),
    "navigator.maxTouchPoints":          navigator.maxTouchPoints,
    "window.TouchEvent":                 "TouchEvent" in window,
    "window.Touch":                      "Touch" in window,
    "window.TouchList":                  "TouchList" in window,
    "document.createTouch":              typeof document.createTouch === "function",
    "document.createEvent('TouchEvent')": createEvent,
    "'ontouchstart' in window":          "ontouchstart" in window,
    "'ontouchstart' in document":        "ontouchstart" in document,
    "'ontouchstart' in documentElement": "ontouchstart" in document.documentElement,
    "screen.orientation.type":           screen.orientation.type,
    "screen.orientation.angle":          screen.orientation.angle,
  };
}"""

VIEWPORT_JS = "() => document.documentElement.clientWidth"


def page_with_meta(content: Optional[str]) -> str:
    meta = f'<meta name="viewport" content="{content}">' if content else ""
    body = '<body style="margin:0"><div style="height:2000px"></div></body>'
    return "data:text/html," + quote(f"<!doctype html><html><head>{meta}</head>{body}</html>")


# Layout width a page with this meta viewport gets, relative to the window.
# None means "the window's own width".
META_CASES = {
    "width=device-width": None,
    "width=600": 600,
    "no meta tag": 980,
}


def resolve_binary(argv) -> Optional[Path]:
    if "--binary" in argv:
        return Path(argv[argv.index("--binary") + 1]).resolve()
    if os.environ.get("CAMOUFOX_BINARY"):
        return Path(os.environ["CAMOUFOX_BINARY"]).resolve()
    matches = sorted(REPO_ROOT.glob("camoufox-*/obj-*/dist/bin/camoufox-bin"))
    return matches[-1] if matches else None


async def read_viewports(page, window_width: int, mobile: bool) -> Dict[str, Any]:
    """clientWidth for each META_CASES page, with what that page should get."""
    results = {}
    for name, width in META_CASES.items():
        await page.goto(page_with_meta(None if name == "no meta tag" else name))
        got = await page.evaluate(VIEWPORT_JS)
        want = (width or window_width) if mobile else window_width
        results[f"layout width, {name}"] = (got, want)
    return results


def compare(title: str, actual: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    print(f"\n=== {title} ===")
    width = max(len(k) for k in expected)
    failures = 0
    for name, want in expected.items():
        got = actual.get(name, "<missing>")
        if isinstance(want, tuple):
            got, want = want
        ok = got == want
        failures += not ok
        detail = f"{str(got):<7}" if ok else f"{str(got):<7} (expected {want})"
        print(f"    [{'ok  ' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print(f"  {len(expected) - failures}/{len(expected)} match")
    return failures == 0


async def run(binary: Path) -> bool:
    from playwright.async_api import async_playwright

    ok = True
    viewport = {"width": 412, "height": 800}
    async with async_playwright() as p:

        async def launch(config: Dict[str, Any]):
            env = dict(os.environ)
            env["CAMOU_CONFIG_1"] = json.dumps(config)
            return await p.firefox.launch(executable_path=str(binary), headless=False, env=env)

        # --- the whole browser in mobile mode ---
        browser = await launch({"mobile": True, "navigator.userAgent": ANDROID_UA})
        try:
            page = await browser.new_page(viewport=viewport)
            await page.goto(page_with_meta("width=device-width"))
            ok &= compare("mobile: touch and pointer", await page.evaluate(PROBE_JS), PHONE)
            vp = await read_viewports(page, viewport["width"], mobile=True)
            ok &= compare("mobile: meta viewport", vp, vp)
            ua = await page.evaluate("() => navigator.userAgent")
            ok &= compare("mobile: user agent", {"navigator.userAgent": ua},
                          {"navigator.userAgent": ANDROID_UA})
        finally:
            await browser.close()

        # --- a desktop launch with one phone context ---
        browser = await launch({})
        try:
            phone = await browser.new_context(
                viewport=viewport, is_mobile=True, has_touch=True, user_agent=ANDROID_UA
            )
            page = await phone.new_page()
            await page.goto(page_with_meta("width=device-width"))
            ok &= compare("per-context phone: touch and pointer",
                          await page.evaluate(PROBE_JS), PHONE)
            vp = await read_viewports(page, viewport["width"], mobile=True)
            ok &= compare("per-context phone: meta viewport", vp, vp)

            # Camoufox defaults to no_viewport when the window is spoofed, which
            # keeps the page out of RDM; touch still has to bring a digitizer.
            touch_only = await browser.new_context(no_viewport=True, has_touch=True)
            page = await touch_only.new_page()
            await page.goto("about:blank")
            got = await page.evaluate(PROBE_JS)
            ok &= compare("has_touch without a viewport", got, {
                "navigator.maxTouchPoints": 5,
                "window.TouchEvent": True,
                "(pointer: coarse)": True,
            })

            plain = await browser.new_context(viewport=viewport)
            page = await plain.new_page()
            await page.goto(page_with_meta("width=device-width"))
            ok &= compare("plain context beside it: touch and pointer",
                          await page.evaluate(PROBE_JS), DESKTOP)
            vp = await read_viewports(page, viewport["width"], mobile=False)
            ok &= compare("plain context beside it: meta viewport ignored", vp, vp)
        finally:
            await browser.close()

        # --- control: nothing asked for a phone ---
        browser = await launch({})
        try:
            page = await browser.new_page(viewport=viewport)
            await page.goto(page_with_meta("width=device-width"))
            ok &= compare("control: touch and pointer", await page.evaluate(PROBE_JS), DESKTOP)
            vp = await read_viewports(page, viewport["width"], mobile=False)
            ok &= compare("control: meta viewport ignored", vp, vp)
        finally:
            await browser.close()
    return ok


async def main() -> int:
    binary = resolve_binary(sys.argv)
    if binary is None or not binary.exists():
        print(f"FATAL: no camoufox binary found (looked for {binary})")
        return 1
    print(f"Binary: {binary}")
    if await run(binary):
        print("\nPASS: mobile mode looks like Firefox for Android; desktop is untouched.")
        return 0
    print("\nFAIL: see the rows marked FAIL above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
