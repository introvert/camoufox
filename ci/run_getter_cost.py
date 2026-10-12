#!/usr/bin/env python3
"""Per-read cost of the spoofed getters, against stock Firefox.

A page times 20 navigator, screen and window getters, each in a loop of reads,
and reports nanoseconds per read. Stock Firefox loads it with no automation
attached; each Camoufox build loads it in a NewContext identity, the way a
fleet runs. The table is each build's overhead over stock per getter.

Reports only, never gates: a shared runner's timings move by more than the
regressions this is for. Run it on a quiet machine for numbers to act on.

    python3 -m ci.run_getter_cost --build this=/path/to/camoufox-bin \\
        --build beta.36=/path/to/old/camoufox-bin [--stock /path/to/firefox]

The stock Firefox defaults to the copy of upstream.sh's release that the
stock-gpu-parity guard downloads and verifies into .ci-work.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import platform
import socket
import statistics
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ._util import WORK_DIR, die, log, read_upstream_sh, summary

GETTERS = [
    "navigator.userAgent", "navigator.platform", "navigator.oscpu",
    "navigator.hardwareConcurrency", "navigator.maxTouchPoints", "navigator.language",
    "screen.width", "screen.height", "screen.availWidth", "screen.availHeight",
    "screen.availTop", "screen.colorDepth", "screen.pixelDepth",
    "devicePixelRatio", "innerWidth", "innerHeight", "outerWidth", "outerHeight",
    "screenX", "screenY",
]
# performance.now() is clamped to a millisecond, so a round has to run long
# enough for that to vanish: 200,000 reads put the step at 5 ns.
READS = 200_000
ROUNDS = 7

# Every read feeds `sink`, which is posted back, so the JIT cannot drop the loop
# (see stock-parity-probes.py). Rounds are interleaved across getters and the
# median kept, so a GC pause or a scheduler stall moves one sample, not the result.
PAGE = """<!doctype html><script>
const getters = %GETTERS%;
const fns = getters.map(g => new Function(`return ${g}`));
let sink = 0;
const samples = getters.map(() => []);
setTimeout(() => {
  for (let r = 0; r < %ROUNDS%; r++) {
    fns.forEach((fn, i) => {
      const t = performance.now();
      for (let n = 0; n < %READS%; n++) { const v = fn(); sink += typeof v === 'string' ? v.length : v; }
      samples[i].push((performance.now() - t) * 1e6 / %READS%);
    });
  }
  fetch('/result', {method: 'POST', body: JSON.stringify({samples, sink: String(sink)})});
}, 500);
</script>"""


def serve(page: bytes) -> Tuple[http.server.ThreadingHTTPServer, int, Dict[str, Any]]:
    state: Dict[str, Any] = {"done": threading.Event()}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            state["result"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.end_headers()
            state["done"].set()

        def log_message(self, *args):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port, state


def wait_result(state, what: str) -> List[List[float]]:
    if not state["done"].wait(180):
        die(f"{what}: the page never reported")
    return state["result"]["samples"]


def measure_stock(firefox: Path, url: str, state) -> List[List[float]]:
    with tempfile.TemporaryDirectory(prefix="getter-cost-") as profile:
        Path(profile, "user.js").write_text(
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
        )
        proc = subprocess.Popen(
            [str(firefox), "-headless", "-no-remote", "-profile", profile, url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            return wait_result(state, f"stock {firefox}")
        finally:
            proc.kill()
            proc.wait()


def measure_camoufox(binary: Path, url: str, state) -> List[List[float]]:
    from camoufox.sync_api import Camoufox, NewContext

    with Camoufox(headless=True, executable_path=str(binary)) as browser:
        page = NewContext(browser).new_page()
        page.goto(url)
        return wait_result(state, str(binary))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stock", type=Path, help="stock Firefox executable")
    ap.add_argument("--build", action="append", required=True, metavar="NAME=PATH",
                    help="a Camoufox binary to measure; repeat to compare builds")
    ap.add_argument("--passes", type=int, default=3, help="launches per browser, interleaved")
    args = ap.parse_args()

    stock = args.stock or (WORK_DIR / "stock-firefox" / read_upstream_sh()["version"]
                           / platform.machine() / "firefox" / "firefox")
    if not stock.exists():
        die(f"no stock Firefox at {stock}: run the stock-gpu-parity guard first, or pass --stock")
    builds = [tuple(b.split("=", 1)) for b in args.build]
    page = (PAGE.replace("%GETTERS%", json.dumps(GETTERS))
            .replace("%ROUNDS%", str(ROUNDS)).replace("%READS%", str(READS)))
    samples: Dict[str, List[List[float]]] = {name: [[] for _ in GETTERS] for name in ["stock", *dict(builds)]}

    for p in range(args.passes):
        for name, run in [("stock", lambda url, st: measure_stock(stock, url, st)),
                          *[(n, lambda url, st, b=Path(b): measure_camoufox(b, url, st)) for n, b in builds]]:
            server, port, state = serve(page.encode())
            try:
                log(f"pass {p + 1}/{args.passes}: {name}")
                for i, s in enumerate(run(f"http://127.0.0.1:{port}/", state)):
                    samples[name][i].extend(s)
            finally:
                server.shutdown()

    ns = {name: [statistics.median(s) for s in per] for name, per in samples.items()}
    base = ns["stock"]
    names = [n for n, _ in builds]
    lines = [
        "### Spoofed getter cost: ns per read over stock Firefox",
        "",
        "| Getter | stock | " + " | ".join(names) + " |",
        "|---|---:|" + "---:|" * len(names),
    ]
    for i, getter in enumerate(GETTERS):
        cells = [f"{ns[n][i] - base[i]:+.0f}" for n in names]
        lines.append(f"| `{getter}` | {base[i]:.0f} | " + " | ".join(cells) + " |")
    total_stock = sum(base)
    cells = [f"{sum(ns[n]) - total_stock:+.0f} ({sum(ns[n]) / total_stock - 1:+.0%})" for n in names]
    lines.append(f"| **Total, {len(GETTERS)} getters** | {total_stock:.0f} | " + " | ".join(cells) + " |")
    lines += ["", f"Median of {ROUNDS * args.passes} rounds of {READS:,} reads per getter, "
              f"{args.passes} launches per browser."]
    table = "\n".join(lines)
    # The log as well as the job summary: the log is what the API serves.
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        print(table, flush=True)
    summary(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
