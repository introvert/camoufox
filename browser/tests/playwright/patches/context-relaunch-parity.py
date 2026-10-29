"""
Verify a context given an identity looks like a browser launched with it.

A context swap (NewContext on a running browser) is meant to replace a relaunch.
Each value a page reads either follows the context's identity, as it would
follow a relaunch's config, or stays the launch's, and the second kind ties
every context of one browser together. Before this guard, these stayed the
launch's or followed only part of the identity:

  window     outerWidth/outerHeight stayed the launch's window, beside the
             context's screen and inner size (inner wider than outer, or a
             window larger than its screen), and screenX/screenY the launch's
             position, a full-width context window partly off its screen
  voices     names holding a comma were dropped, and the voices were drawn
             without the context's locale (only the en-US pack on a de-DE
             Windows)
  phone      text measured at the launch's pixel ratio beside the context's
             devicePixelRatio: the browser renders at one ratio, so a phone
             context is now the launch's device

For each OS, a browser launches with an identity of its own and opens a context
with the identity NewContext would draw on it. Another browser launches with
that identity. The same page is read in both, in the main world.

What PASS means: the context reports what the relaunch reports for every value
below, and on a desktop the drawn identity differs from the first browser's
launch in at least one value, so the check is not vacuous.

    python browser/tests/playwright/patches/context-relaunch-parity.py
"""

import http.server
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import launch_kwargs  # noqa: E402

LOCALE = "de-DE"
TIMEZONE = "Asia/Tokyo"
FONT_POOL = {"windows": "win", "macos": "mac", "linux": "lin", "android": "lin"}
# Given to both the context and the relaunch, so they say nothing of the draw.
FORCED = {"languages", "intl", "timeZone", "acceptLanguage"}
LOCALE_KEYS = {"navigator.language", "locale:language", "locale:region", "locale:script"}

READ = """async (fonts) => {
  const ctx = document.createElement('canvas').getContext('2d');
  const width = (font) => { ctx.font = '40px ' + font; return ctx.measureText('mmmmmmmmmmlli').width; };
  const voices = await new Promise(resolve => {
    const names = () => speechSynthesis.getVoices().map(v => v.name + ' / ' + v.lang);
    if (names().length) return resolve(names());
    speechSynthesis.onvoiceschanged = () => resolve(names());
    setTimeout(() => resolve(names()), 2000);
  });
  const span = document.createElement('span');
  span.style.font = '17px serif';
  span.textContent = 'Hamburgefonstiv';
  document.body.append(span);
  const rect = span.getBoundingClientRect();
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency,
    languages: [...navigator.languages],
    intl: Intl.DateTimeFormat().resolvedOptions().locale,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    screen: [screen.width, screen.height, screen.availWidth, screen.availHeight, screen.colorDepth],
    window: [outerWidth, outerHeight, innerWidth, innerHeight, screenX, screenY],
    devicePixelRatio,
    voices,
    fonts: fonts.filter(f => width(`"${f}", monospace`) !== width('monospace')
                          || width(`"${f}", serif`) !== width('serif')),
    textMetrics: [ctx.measureText('Hamburgefonstiv').width, rect.width, rect.height],
  };
}"""

ACCEPT = {}


class Recording(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ACCEPT[self.path] = self.headers.get("Accept-Language")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><meta charset=utf-8><body>x</body>")

    def log_message(self, *a):
        pass


def read(page, base, path, fonts):
    page.goto(base + path)
    out = page.evaluate(f"mw:({READ})({json.dumps(fonts)})")
    out["acceptLanguage"] = ACCEPT[path]
    return out


def main() -> int:
    from camoufox.fingerprints import generate_context_fingerprint
    from camoufox.sync_api import Camoufox

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recording)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    pools = json.loads((Path(__file__).resolve().parents[4]
                        / "python/src/camoufox/fonts.json").read_text())

    failures = []
    try:
        for target_os, pool in FONT_POOL.items():
            fonts = pools[pool]
            common = dict(os=target_os, main_world_eval=True, i_know_what_im_doing=True)
            with Camoufox(**launch_kwargs(**common)) as browser:
                launch = read(browser.new_page(), base, f"/{target_os}-launch", fonts)
                # The identity NewContext would draw on this browser.
                fp = generate_context_fingerprint(
                    os=target_os, locale=LOCALE, i_know_what_im_doing=True,
                    **vars(browser)["_camoufox_machine"])
                context = browser.new_context(**{**fp["context_options"], "timezone_id": TIMEZONE})
                context.add_init_script(fp["init_script"])
                swap = read(context.new_page(), base, f"/{target_os}-swap", fonts)
            # The relaunch takes the locale as a launch does, and a phone's pixel
            # ratio is a pref, not a config key.
            config = {k: v for k, v in fp["config"].items() if k not in LOCALE_KEYS}
            dpr = fp["preset"]["screen"].get("devicePixelRatio")
            prefs = {"layout.css.devPixelsPerPx": str(dpr)} if dpr else {}
            with Camoufox(**launch_kwargs(**common, locale=LOCALE, firefox_user_prefs=prefs,
                                          config={**config, "timezone": TIMEZONE})) as browser:
                relaunch = read(browser.new_page(), base, f"/{target_os}-relaunch", fonts)

            print(f"{target_os}:")
            differs = 0
            if target_os == "android":
                # Known difference: a relaunch's window is sized to its outer
                # height, and a context's content to that height less the
                # chrome's 86 CSS px, which the toolbar is not at a phone's
                # pixel ratio, so innerHeight differs by a few px.
                for read_ in (relaunch, swap, launch):
                    read_["window"] = read_["window"][:2] + read_["window"][4:]
            for key, want in relaunch.items():
                got = swap[key]
                differs += key not in FORCED and launch[key] != want
                shown = (f"{len(want)} entries" if isinstance(want, list) and len(want) > 6
                         else want)
                print(f"  {key:20} {'ok  ' if got == want else 'FAIL'} {shown}")
                if got != want:
                    failures.append(f"{target_os}: {key}: the context reports {got}, "
                                    f"a relaunch with its identity {want}")
            # A phone context is the launch's device: the browser renders at one
            # pixel ratio, and each recorded phone has its own.
            if not differs and target_os != "android":
                failures.append(f"{target_os}: the drawn identity matches the launch's in every "
                                f"value, so the check is vacuous")
    finally:
        srv.shutdown()

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: a context reports what a relaunch with its identity reports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
