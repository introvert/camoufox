"""
Verify what follows from a context's own user agent and locale
(per-context-consistency.patch).

  appVersion  an Android context of a Windows launch reports the appVersion its
              own user agent implies, in its window and its workers; it used to
              keep the launch's "5.0 (Windows)"
  wire        a worker's request sends the context's user agent: worker
              requests have no browsing context to carry Playwright's override
              and went out with the launch-wide one
  languages   a context created with locale "de-DE" reports navigator.languages
              ["de-DE", "de"] in its window and workers and sends that list in
              Accept-Language, as the launch-wide locale does; the override was
              used verbatim before

    python browser/tests/playwright/patches/context-identity-follows.py
"""

import functools
import http.server
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import resolve_binary  # noqa: E402

LOCALE = "de-DE"
LANGUAGES = ["de-DE", "de"]

PROBE = """async () => {
  const read = () => ({appVersion: navigator.appVersion, languages: [...navigator.languages]});
  // A blob: worker cannot resolve a relative URL.
  const code = 'onmessage = async () => { await fetch("' + location.origin + '/from-worker");'
             + ' postMessage((' + read.toString() + ')()); }';
  const w = new Worker(URL.createObjectURL(new Blob([code])));
  const worker = await new Promise(r => { w.onmessage = e => r(e.data); w.postMessage(0); });
  return {userAgent: navigator.userAgent, window: read(), worker};
}"""

SEEN = {}


class Recording(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        SEEN[path] = dict(self.headers)
        if path == "/from-worker":
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *a):
        pass


def main() -> int:
    from camoufox.sync_api import Camoufox
    from camoufox.fingerprints import _app_version_from_user_agent, generate_context_fingerprint

    binary = resolve_binary()
    site = tempfile.mkdtemp()
    Path(site, "p.html").write_text("<!doctype html><meta charset=utf-8><body>x</body>")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Recording, directory=site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/p.html"

    fp = generate_context_fingerprint(os="android", locale=LOCALE, i_know_what_im_doing=True)
    try:
        with Camoufox(os="windows", headless=True, executable_path=str(binary),
                      i_know_what_im_doing=True) as browser:
            ctx = browser.new_context(**fp["context_options"])
            ctx.add_init_script(fp["init_script"])
            page = ctx.new_page()
            page.goto(url)
            out = page.evaluate(PROBE)
    finally:
        srv.shutdown()

    ua = out["userAgent"]
    page_headers = SEEN.get("/p.html", {})
    worker_headers = SEEN.get("/from-worker", {})
    print(f"  userAgent            : {ua}")
    print(f"  window               : {out['window']}")
    print(f"  worker               : {out['worker']}")
    print(f"  page Accept-Language : {page_headers.get('Accept-Language')}")
    print(f"  worker User-Agent    : {worker_headers.get('User-Agent')}")

    failures = []
    if "Android" not in ua:
        failures.append(f"the context's user agent did not apply -- check is vacuous: {ua}")
    else:
        expected = _app_version_from_user_agent(ua)
        for scope in ("window", "worker"):
            if out[scope]["appVersion"] != expected:
                failures.append(f"appVersion: {scope} reports {out[scope]['appVersion']!r}, the UA implies {expected!r}")
        if worker_headers.get("User-Agent") != ua:
            failures.append(f"wire: the worker's request sent {worker_headers.get('User-Agent')!r}")
    for scope in ("window", "worker"):
        if out[scope]["languages"] != LANGUAGES:
            failures.append(f"languages: {scope} reports {out[scope]['languages']}, expected {LANGUAGES}")
    if not (page_headers.get("Accept-Language") or "").startswith("de-DE,de;q="):
        failures.append(f"languages: Accept-Language {page_headers.get('Accept-Language')!r}")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: appVersion, worker requests and the language list follow the context's own values.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
