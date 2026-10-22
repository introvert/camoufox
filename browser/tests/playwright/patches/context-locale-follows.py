"""
Verify a context's locale and timezone reach every scope of its pages, its
locale every request they send, and its language the strings Firefox shows them
(per-context-consistency.patch).

With a launch locale of en-US and a context created with locale "de-DE", a page
read de-DE in its own realm but en-US where a realm has no override of its own
or no browsing context to take one from:

  isolated   page.evaluate's isolated world formatted with the launch locale,
             so the page and a Worker it started seemed to disagree
  worklet    an AudioWorklet's Intl
  service    a service worker's navigator.languages and Intl
  wire       Accept-Language on a worker's script and on the requests of shared
             and service workers, which have no browsing context
  ui         validation messages and XML parse errors, worded in the launch's
             language by every content process

What PASS means: every scope reports de-DE and Asia/Tokyo (the launch says
America/New_York), every request sends the de-DE language list, and the
context's built-in strings are a de-DE launch's, both in a page whose process
started before the context's locale was known (about:blank) and in one started
after.

    python browser/tests/playwright/patches/context-locale-follows.py
"""

import functools
import http.server
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import launch_kwargs  # noqa: E402

LOCALE = "de-DE"
TIMEZONE = "Asia/Tokyo"

READ = """() => ({
  languages: typeof navigator === 'undefined' ? null : [...navigator.languages],
  locale: new Intl.DateTimeFormat().resolvedOptions().locale,
  timeZone: new Intl.DateTimeFormat().resolvedOptions().timeZone,
})"""

# One script for every kind of worker; each reports after a request of its own.
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

WORKLET = f"""const read = {READ};
registerProcessor('probe', class extends AudioWorkletProcessor {{
  constructor() {{ super(); this.port.postMessage(read()); }}
  process() {{ return false; }}
}});
"""

PROBE = f"""mw:(async () => {{
  const read = {READ};
  const out = {{window: read()}};
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
  const audio = new OfflineAudioContext(1, 128, 44100);
  await audio.audioWorklet.addModule('/worklet.js');
  out.worklet = await new Promise(r => {{ new AudioWorkletNode(audio, 'probe').port.onmessage = e => r(e.data); }});
  return out;
}})()"""

# Strings Firefox words in the app locale's language, not the page's.
UI = """() => {
  const input = document.createElement('input');
  input.required = true;
  const email = document.createElement('input');
  email.type = 'email';
  email.value = 'x';
  const xml = new DOMParser().parseFromString('<a>', 'text/xml');
  return {required: input.validationMessage, email: email.validationMessage,
          xml: xml.querySelector('parsererror').firstChild.textContent.split('\\n')[0]};
}"""

SEEN = {}


class Recording(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        SEEN[path] = self.headers.get("Accept-Language")
        if path.startswith("/from-"):
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *a):
        pass


def main() -> int:
    from camoufox.sync_api import Camoufox

    site = tempfile.mkdtemp()
    Path(site, "p.html").write_text("<!doctype html><meta charset=utf-8><body>x</body>")
    Path(site, "w.js").write_text(WORKER)
    Path(site, "worklet.js").write_text(WORKLET)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Recording, directory=site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    try:
        with Camoufox(**launch_kwargs(locale=LOCALE, i_know_what_im_doing=True)) as browser:
            expected_ui = browser.new_page().evaluate(UI)
        with Camoufox(**launch_kwargs(locale="en-US", config={"timezone": "America/New_York"},
                                      main_world_eval=True, i_know_what_im_doing=True)) as browser:
            page = browser.new_context(locale=LOCALE, timezone_id=TIMEZONE).new_page()
            ui = {"about:blank": page.evaluate(UI)}
            page.goto(f"http://127.0.0.1:{srv.server_address[1]}/p.html")
            ui["page"] = page.evaluate(UI)
            out = page.evaluate(PROBE)
            out["isolated"] = page.evaluate(f"({READ})()")
    finally:
        srv.shutdown()

    failures = []
    for scope, got in out.items():
        print(f"  {scope:10}: {got}")
        if got["locale"] != LOCALE or got["timeZone"] != TIMEZONE:
            failures.append(f"{scope}: Intl reports {got['locale']} / {got['timeZone']}")
        if got["languages"] not in (None, [LOCALE, "de"]):
            failures.append(f"{scope}: navigator.languages {got['languages']}")
    for path, accept in sorted(SEEN.items()):
        print(f"  {path:15}: Accept-Language {accept}")
        if not (accept or "").startswith("de-DE,de;q="):
            failures.append(f"wire: {path} sent Accept-Language {accept!r}")
    if not {"/w.js", "/from-dedicated", "/from-shared", "/from-service"} <= SEEN.keys():
        failures.append(f"a request never arrived -- check is vacuous: {sorted(SEEN)}")
    print(f"  {LOCALE} launch: {expected_ui}")
    for where, got in ui.items():
        print(f"  {where:15}: {got}")
        if got != expected_ui:
            failures.append(f"ui: {where} words its strings as {got}, a {LOCALE} launch as {expected_ui}")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: every scope, request and built-in string follows the context's locale and timezone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
