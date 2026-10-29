"""
Verify a new context can take a whole other identity without a relaunch, and
that nothing of the launch's shows through it.

One browser launches as a Windows machine (en-US, America/New_York, its own
GPU). A context then opens as a macOS machine with another GPU, de-DE,
Asia/Tokyo and its own position. Every value it claims must agree in every
scope a page can reach, and on the wire:

  scopes   page main world, the isolated world page.evaluate runs in, a
           same-origin iframe, a cross-site iframe, a dedicated, a shared and a
           service worker
  values   userAgent, appVersion, platform, hardwareConcurrency, languages,
           Intl locale and time zone, the clock's UTC offset, screen size,
           geolocation, and WebGL: vendor, renderer, the device limits, the
           extension list and shader precision (pages and the dedicated
           worker's OffscreenCanvas)
  wire     User-Agent and Accept-Language on the page, both iframes, every
           worker script and a request from each worker

The WebGL limits, extensions and precisions used to stay the launch GPU's beside
the context's renderer (webgl-spoofing.patch, setWebGLConfig).

What PASS means: every scope and request reports the context's values; a page of
a default context of the same browser reports the launch's, so the two really
differ and the check is not vacuous.

    python browser/tests/playwright/patches/context-identity-swap.py
"""

import functools
import http.server
import sys
import tempfile
import threading
from pathlib import Path

import orjson

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import resolve_binary  # noqa: E402

LOCALE = "de-DE"
LANGUAGES = ["de-DE", "de"]
TIMEZONE = "Asia/Tokyo"
GEO = {"latitude": 35.6762, "longitude": 139.6503, "accuracy": 25}

# The WebGL1 limits webgl-spoofing.patch answers from the identity's table.
LIMITS = [3379, 3386, 3408, 33901, 33902, 34024, 34076, 34921, 34930, 35660, 35661, 36347, 36348, 36349]
VERTEX_HIGH_FLOAT = "35633,36338"

# One reader for every scope; WebGL where the scope can draw.
READ = f"""() => {{
  const gl = (() => {{
    const canvas = typeof document !== 'undefined' ? document.createElement('canvas')
      : typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(8, 8) : null;
    const g = canvas && canvas.getContext('webgl');
    if (!g) return null;
    const info = g.getExtension('WEBGL_debug_renderer_info');
    const limits = {{}};
    for (const p of {LIMITS}) {{
      const v = g.getParameter(p);
      // The isolated world reads a page's typed arrays through an Xray,
      // which refuses to copy them.
      try {{ limits[p] = v && typeof v === 'object' ? Array.from(v) : v; }} catch {{ limits[p] = 'xray'; }}
    }}
    const f = g.getShaderPrecisionFormat(g.VERTEX_SHADER, g.HIGH_FLOAT);
    return {{
      vendor: info && g.getParameter(info.UNMASKED_VENDOR_WEBGL),
      renderer: info && g.getParameter(info.UNMASKED_RENDERER_WEBGL),
      limits, extensions: g.getSupportedExtensions(),
      precision: [f.rangeMin, f.rangeMax, f.precision],
    }};
  }})();
  return {{
    userAgent: navigator.userAgent, appVersion: navigator.appVersion,
    platform: navigator.platform, cores: navigator.hardwareConcurrency,
    languages: [...navigator.languages],
    locale: new Intl.DateTimeFormat().resolvedOptions().locale,
    timeZone: new Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date(Date.UTC(2026, 0, 15)).getTimezoneOffset(),
    screen: typeof screen === 'undefined' ? null : [screen.width, screen.height],
    gl,
  }};
}}"""

WORKER = f"""const read = {READ};
const report = async (kind) => {{ await fetch('/from-' + kind); return read(); }};
onmessage = async (e) => {{
  if (e.source) e.source.postMessage(await report('service'));
  else postMessage(await report('dedicated'));
}};
onconnect = (e) => {{
  const port = e.ports[0];
  port.onmessage = async () => port.postMessage(await report('shared'));
}};
"""

PROBE = f"""mw:(async () => {{
  const read = {READ};
  const out = {{window: read()}};
  out.position = await new Promise(r => navigator.geolocation.getCurrentPosition(
    p => r([p.coords.latitude, p.coords.longitude]), e => r(String(e.message))));
  const frame = (src) => new Promise(r => {{
    const f = document.createElement('iframe');
    onmessage = e => r(e.data);
    f.src = src;
    document.body.append(f);
  }});
  out.iframe = await frame('/frame.html');
  out.crossSiteIframe = await frame(location.href.replace('127.0.0.1', 'localhost').replace('p.html', 'frame.html'));
  out.dedicated = await new Promise(r => {{
    const w = new Worker('/w.js'); w.onmessage = e => r(e.data); w.postMessage(0);
  }});
  out.shared = await new Promise(r => {{
    const s = new SharedWorker('/w.js'); s.port.onmessage = e => r(e.data); s.port.postMessage(0);
  }});
  const reg = await navigator.serviceWorker.register('/w.js');
  const sw = reg.installing || reg.waiting || reg.active;
  if (sw.state !== 'activated')
    await new Promise(r => sw.addEventListener('statechange', () => sw.state === 'activated' && r()));
  out.service = await new Promise(r => {{
    navigator.serviceWorker.onmessage = e => r(e.data); sw.postMessage(0);
  }});
  return out;
}})()"""

FRAME = f"""<!doctype html><meta charset=utf-8><script>
parent.postMessage(({READ})(), '*');
</script>"""

SEEN = {}


class Recording(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        SEEN[f"{self.headers.get('Host', '').split(':')[0]}{path}"] = (
            self.headers.get("User-Agent"), self.headers.get("Accept-Language"))
        if path.startswith("/from-"):
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *a):
        pass


def expected_identity(config):
    from camoufox.fingerprints import _app_version_from_user_agent

    tables = config.get("webGl:parameters", {})
    precision = config["webGl:shaderPrecisionFormats"][VERTEX_HIGH_FLOAT]
    return {
        "userAgent": config["navigator.userAgent"],
        "appVersion": _app_version_from_user_agent(config["navigator.userAgent"]),
        "platform": config["navigator.platform"],
        "cores": config["navigator.hardwareConcurrency"],
        "screen": [config["screen.width"], config["screen.height"]],
        "gl": {
            "vendor": config["webGl:vendor"],
            "renderer": config["webGl:renderer"],
            "limits": {str(p): tables[str(p)] for p in LIMITS if tables.get(str(p)) is not None},
            "extensions": config["webGl:supportedExtensions"],
            "precision": [precision["rangeMin"], precision["rangeMax"], precision["precision"]],
        },
    }


def mismatches(scope, got, want):
    """Where one scope's reading differs from the identity it should report."""
    out = []
    for key in ("userAgent", "appVersion", "platform", "cores", "languages", "locale", "timeZone", "offset"):
        if key in want and got[key] != want[key]:
            out.append(f"{scope}: {key} {got[key]!r}, expected {want[key]!r}")
    if scope in ("window", "iframe", "crossSiteIframe") and got["screen"] != want["screen"]:
        out.append(f"{scope}: screen {got['screen']}, expected {want['screen']}")
    gl, want_gl = got["gl"], want["gl"]
    if gl is None:
        if scope in ("window", "iframe", "crossSiteIframe", "dedicated"):
            out.append(f"{scope}: no WebGL context -- check is vacuous")
        return out
    for key in ("vendor", "renderer", "extensions", "precision"):
        if gl[key] != want_gl[key]:
            out.append(f"{scope}: WebGL {key} {gl[key]!r}, expected {want_gl[key]!r}")
    for pname, value in want_gl["limits"].items():
        if gl["limits"][pname] != value and not (scope == "isolated" and gl["limits"][pname] == "xray"):
            out.append(f"{scope}: WebGL parameter {pname} {gl['limits'][pname]!r}, expected {value!r}")
    return out


def main() -> int:
    from playwright.sync_api import sync_playwright

    from camoufox.addons import DefaultAddons
    from camoufox.fingerprints import generate_context_fingerprint
    from camoufox.sync_api import NewBrowser
    from camoufox.utils import _camou_config_blob, launch_options
    from camoufox.virtdisplay import VirtualDisplay

    site = tempfile.mkdtemp()
    Path(site, "p.html").write_text("<!doctype html><meta charset=utf-8><body>x</body>")
    Path(site, "frame.html").write_text(FRAME)
    Path(site, "w.js").write_text(WORKER)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Recording, directory=site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/p.html"

    # A real WebGL context needs a display; canvas noise lets a context claim
    # another OS and GPU than the host's.
    display = VirtualDisplay()
    opts = launch_options(
        os="windows", locale="en-US", config={"timezone": "America/New_York"},
        headless=False, virtual_display=display.get(), canvas_noise=True, main_world_eval=True,
        executable_path=str(resolve_binary()), exclude_addons=[DefaultAddons.UBO], i_know_what_im_doing=True,
    )
    launch = orjson.loads(_camou_config_blob(opts))

    with sync_playwright() as pw:
        browser = NewBrowser(pw, from_options=opts)
        ff_version = browser.version.split(".", 1)[0]
        fp = generate_context_fingerprint(os="macos", locale=LOCALE, timezone=TIMEZONE, ff_version=ff_version)
        context = fp["config"]
        try:
            default_page = browser.new_page()
            default_page.goto(url)
            launch_read = default_page.evaluate(f"mw:({READ})()")

            ctx = browser.new_context(**fp["context_options"], geolocation=GEO, permissions=["geolocation"])
            ctx.add_init_script(fp["init_script"])
            page = ctx.new_page()
            SEEN.clear()
            page.goto(url)
            out = page.evaluate(PROBE)
            out["isolated"] = page.evaluate(f"({READ})()")
        finally:
            browser.close()
            display.kill()
            srv.shutdown()

    want = expected_identity(context)
    want.update(languages=LANGUAGES, locale=LOCALE, timeZone=TIMEZONE, offset=-540)
    failures = []

    # Not vacuous: the launch's own page reads the launch, and differs.
    launch_want = expected_identity(launch)
    for key in ("userAgent", "platform"):
        if launch_read[key] != launch_want[key] or launch_want[key] == want[key]:
            failures.append(f"launch page: {key} {launch_read[key]!r} -- check is vacuous")
    for key in ("renderer", "extensions"):
        if launch_read["gl"] is None or launch_read["gl"][key] == want["gl"][key]:
            failures.append(f"launch page: WebGL {key} equals the context's -- check is vacuous")
    if launch_read["timeZone"] == TIMEZONE or launch_read["locale"] == LOCALE:
        failures.append("launch page: already reads the context's locale or time zone -- check is vacuous")

    position = out.pop("position")
    print(f"  {'position':16}: {position}")
    if position != [GEO["latitude"], GEO["longitude"]]:
        failures.append(f"window: geolocation {position}, expected {[GEO['latitude'], GEO['longitude']]}")
    for scope, got in out.items():
        print(f"  {scope:16}: {got['userAgent'][:60]}... {got['locale']} {got['timeZone']} "
              f"{(got['gl'] or {}).get('renderer')}")
        failures += mismatches(scope, got, want)

    for where, (ua, accept) in sorted(SEEN.items()):
        print(f"  {where:28}: {accept}")
        if ua != want["userAgent"]:
            failures.append(f"wire: {where} sent User-Agent {ua!r}")
        if not (accept or "").startswith("de-DE,de;q="):
            failures.append(f"wire: {where} sent Accept-Language {accept!r}")
    requests = {f"127.0.0.1/{p}" for p in ("p.html", "frame.html", "w.js", "from-dedicated", "from-shared",
                                            "from-service")} | {"localhost/frame.html"}
    if not requests <= SEEN.keys():
        failures.append(f"a request never arrived -- check is vacuous: {sorted(requests - SEEN.keys())}")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: a context of another OS, GPU, locale, clock and place reports its own values "
          "in every scope and request, and none of the launch's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
