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

The raw binary, with CAMOU_CONFIG set directly:

    mobile          CAMOU_CONFIG {"mobile": true}   -> every page is the phone
    per-context     a desktop launch, one context with isMobile + hasTouch and
                    an Android user agent -> that context is the phone, a plain
                    one beside it is not
    control         a desktop launch -> nothing moved

Juggler puts a page in Responsive Design Mode only for an isMobile context with
a viewport and an Android user agent (TargetRegistry.js _usesRDM), and RDM is
what gives a per-context phone its meta viewport and window.orientation. RDM
matches no desktop browser, so isMobile on a desktop identity must stay out of
it (viewport-no-rdm.py): the per-context launch also opens an isMobile context
with the desktop user agent, which must answer like the control.

Then the Python package, the way callers ask for a phone:

    Camoufox(os="android")                       -> every page is the phone
    AsyncNewContext(browser, os="android")       -> that context is the phone

The meta viewport needs APZ on the widget, like any MobileViewportManager, so
everything runs headful on a private Xvfb display (helpers.hidden_display).

Run from any venv that has playwright:
    python browser/tests/playwright/patches/mobile-mode.py
    python browser/tests/playwright/patches/mobile-mode.py --binary /path/to/camoufox-bin

Which binary is tested, in order of precedence:
    --binary <path> | $CAMOUFOX_EXECUTABLE_PATH | $CAMOUFOX_BINARY | the in-tree build
"""

import asyncio
import base64
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import hidden_display, resolve_binary  # noqa: E402

import camoufox  # noqa: E402

# The user agent the package's Android persona claims, so the raw-binary
# checks probe the same phone os="android" asks for.
ANDROID_UA = json.loads(
    (Path(camoufox.__file__).parent / "android-devices.json").read_text()
)["navigator"]["userAgent"]

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
    # Compiled out of desktop Firefox; window-orientation.patch exposes it for
    # a phone and keeps it in step with screen.orientation.
    "window.orientation": 0,
    "'onorientationchange' in window": True,
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
    "window.orientation": None,
    "'onorientationchange' in window": False,
    # Desktop Firefox has no such handler, so the content attribute is inert.
    "<body onorientationchange> fires": False,
}

PROBE_JS = r"""() => {
  const mq = q => window.matchMedia(q).matches;
  let createEvent = false;
  try { createEvent = !!document.createEvent('TouchEvent'); } catch (e) { createEvent = false; }
  let bodyFires = false;
  if (document.body) {
    window.__camouOrientationProbe = false;
    document.body.setAttribute("onorientationchange", "window.__camouOrientationProbe = true");
    window.dispatchEvent(new Event("orientationchange"));
    bodyFires = window.__camouOrientationProbe;
    document.body.removeAttribute("onorientationchange");
  }
  return {
    "<body onorientationchange> fires": bodyFires,
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
    "window.orientation":                "orientation" in window ? window.orientation : null,
    "'onorientationchange' in window":   "onorientationchange" in window,
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


# No meta viewport, content wider than the screen: B sits past the right edge
# of a 412px screen at 1:1, so it is only reachable once the page is zoomed out.
WIDE_PAGE = (
    '<!doctype html><html><head></head><body style="margin:0">'
    '<button id="a" style="position:absolute;left:40px;top:40px;width:80px;height:40px;'
    'background:#fff">A</button>'
    '<button id="b" style="position:absolute;left:700px;top:600px;width:160px;height:80px;'
    'background:#ff0000;border:0">B</button>'
    '<div style="width:960px;height:1400px"></div></body></html>'
)


async def probe_wide_page(page, viewport) -> Dict[str, Any]:
    """Zoom state, Playwright clicks and a viewport screenshot on WIDE_PAGE."""
    await page.goto("data:text/html," + quote(WIDE_PAGE))
    await page.evaluate("""() => {
      for (const id of ["a", "b"])
        document.getElementById(id).addEventListener("click",
          () => document.body.dataset["hit" + id] = "1");
    }""")
    result: Dict[str, Any] = {
        "zoomed out": await page.evaluate("() => visualViewport.scale < 1"),
    }
    for sel, key in (("#a", "clicked A (on screen at 1:1)"),
                     ("#b", "clicked B (past the screen edge at 1:1)")):
        try:
            await page.click(sel, timeout=5000)
        except Exception:
            pass
        result[key] = await page.evaluate(
            f"() => document.body.dataset.hit{sel[1:]} === '1'")

    png = await page.screenshot()
    width, height = struct.unpack(">II", png[16:24])
    dpr = await page.evaluate("() => devicePixelRatio")
    result["screenshot is the viewport size"] = (
        abs(width - viewport["width"] * dpr) <= 1 and abs(height - viewport["height"] * dpr) <= 1)
    # B is pure red; at 1:1 it would be off the right edge of the capture.
    result["screenshot shows B"] = await page.evaluate("""async (b64) => {
      const img = new Image();
      img.src = "data:image/png;base64," + b64;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width; c.height = img.height;
      const ctx = c.getContext("2d");
      ctx.drawImage(img, 0, 0);
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      for (let i = 0; i < d.length; i += 4)
        if (d[i] > 240 && d[i + 1] < 20 && d[i + 2] < 20) return true;
      return false;
    }""", base64.b64encode(png).decode())
    return result


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

        # --- zoom: a page wider than the screen is zoomed out to fit, like
        # Android, and Playwright still clicks and screenshots what is shown ---
        browser = await launch({"mobile": True, "navigator.userAgent": ANDROID_UA})
        try:
            page = await browser.new_page(viewport=viewport)
            ok &= compare("zoom, clicks and screenshot", await probe_wide_page(page, viewport), {
                "zoomed out": True,
                "clicked A (on screen at 1:1)": True,
                "clicked B (past the screen edge at 1:1)": True,
                "screenshot is the viewport size": True,
                "screenshot shows B": True,
            })
        finally:
            await browser.close()

        # --- mobile:zoom: false opts out of zooming ---
        browser = await launch({"mobile": True, "mobile:zoom": False,
                                "navigator.userAgent": ANDROID_UA})
        try:
            page = await browser.new_page(viewport=viewport)
            await page.goto(page_with_meta(None))
            got = await page.evaluate("() => visualViewport.scale")
            ok &= compare("mobile:zoom false", {"scale": got}, {"scale": 1})
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
            ok &= compare("per-context phone: zoom, clicks and screenshot",
                          await probe_wide_page(page, viewport), {
                "zoomed out": True,
                "clicked A (on screen at 1:1)": True,
                "clicked B (past the screen edge at 1:1)": True,
                "screenshot is the viewport size": True,
                "screenshot shows B": True,
            })

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

            # isMobile with the desktop user agent: no Android UA, so no RDM.
            # Gecko gates the meta viewport and window.orientation on RDM, and
            # touch on hasTouch, so this context answers like a desktop.
            desktop_mobile = await browser.new_context(viewport=viewport, is_mobile=True)
            page = await desktop_mobile.new_page()
            await page.goto(page_with_meta("width=device-width"))
            ok &= compare("isMobile with a desktop UA: touch and pointer",
                          await page.evaluate(PROBE_JS), DESKTOP)
            vp = await read_viewports(page, viewport["width"], mobile=False)
            ok &= compare("isMobile with a desktop UA: meta viewport ignored", vp, vp)

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


async def run_package(binary: Path) -> bool:
    """The phone the Python package draws for os="android", launch-wide and
    per context."""
    from camoufox.async_api import AsyncCamoufox, AsyncNewContext

    ok = True
    # i_know_what_im_doing: the phone's fonts, voices and GPU come from the
    # Linux pools, which the launcher warns about; that is not under test.
    async with AsyncCamoufox(os="android", headless=False, executable_path=str(binary),
                             i_know_what_im_doing=True) as browser:
        page = await browser.new_page()
        await page.goto(page_with_meta("width=device-width"))
        ok &= compare("package, os='android': touch and pointer",
                      await page.evaluate(PROBE_JS), PHONE)
        # The launcher writes the build's own Firefox version into the UA, so
        # only the platform part is the persona's.
        ua = await page.evaluate("() => navigator.userAgent")
        ok &= compare("package, os='android': user agent",
                      {"claims Firefox for Android": ua.startswith("Mozilla/5.0 (Android ")},
                      {"claims Firefox for Android": True})

    async with AsyncCamoufox(os="linux", headless=False, executable_path=str(binary),
                             i_know_what_im_doing=True) as browser:
        context = await AsyncNewContext(browser, os="android")
        page = await context.new_page()
        await page.goto(page_with_meta("width=device-width"))
        ok &= compare("package, NewContext(os='android'): touch and pointer",
                      await page.evaluate(PROBE_JS), PHONE)
        width = await page.evaluate("() => innerWidth")
        vp = await read_viewports(page, width, mobile=True)
        ok &= compare("package, NewContext(os='android'): meta viewport", vp, vp)
    return ok


async def main() -> int:
    binary = resolve_binary()
    if not binary.exists():
        print(f"FATAL: no camoufox binary found (looked for {binary})")
        return 1
    print(f"Binary: {binary}")
    with hidden_display():
        ok = await run(binary)
        ok &= await run_package(binary)
    if ok:
        print("\nPASS: mobile mode looks like Firefox for Android; desktop is untouched.")
        return 0
    print("\nFAIL: see the rows marked FAIL above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
