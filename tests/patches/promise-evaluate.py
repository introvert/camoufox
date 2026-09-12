"""
Verify page.evaluate() returns when the evaluated code produces a Promise.

Firefox 155 removed Debugger.prototype.onPromiseSettled (Bug 2044167). Juggler's
Runtime.js awaited promise results by assigning that hook, and on a Debugger
object the assignment is not an error -- it just creates a plain property that
nothing ever calls. Every Promise-returning evaluate therefore parked forever in
Runtime._awaitPromise with no error, no timeout and no protocol traffic:

    page.evaluate("({ok: 42})")          # returned
    page.evaluate("(async () => 42)()")  # hung until the caller's timeout

Runtime.js now attaches reactions inside the debuggee that run a `debugger;`
statement when the promise settles, and sweeps the pending set from
onDebuggerStatement. Because page.evaluate() runs in Camoufox's isolated world,
those reactions use the sandbox's own Promise and never touch the page's -- the
page sees neither the `then` call nor the callbacks. That part is asserted here
too: with the default world isolation on, attaching them must stay invisible.

Run against a specific build:
    CAMOUFOX_EXECUTABLE_PATH=/path/to/camoufox-bin python tests/patches/promise-evaluate.py
(without the env var it uses the camoufox-managed browser download.)

What PASS means:
    * already-settled, microtask-settled and timer-settled promises all resolve;
    * a rejected promise surfaces as an error carrying the original message,
      rather than hanging or resolving to None;
    * several promises awaited at once all come back;
    * promises settle in workers, which have no Xrays, as well as in pages;
    * and the page's own Promise.prototype.then is never called on its behalf.
"""

import asyncio
import os
import sys
from typing import Any, Dict

from camoufox.async_api import AsyncCamoufox

EXECUTABLE_PATH = os.environ.get("CAMOUFOX_EXECUTABLE_PATH")

# Timeout per evaluate. Every one of these settles in milliseconds when the
# hook works; the bug makes them wait forever, so anything generous is enough.
TIMEOUT = 20

PAGE = """
<main id="out">idle</main>
<script>
  // A detector's trap. The automation must never route its promise bookkeeping
  // through the page's Promise machinery, so both counters stay at zero.
  window.thenCalls = 0;
  window.thenArgs = '';
  const realThen = Promise.prototype.then;
  Promise.prototype.then = function (...args) {
    window.thenCalls++;
    window.thenArgs = args
      .map(a => (typeof a === 'function' ? a.toString() : String(a)))
      .join('|');
    return realThen.apply(this, args);
  };
  window.ctorReads = 0;
  Object.defineProperty(Promise.prototype, 'constructor', {
    configurable: true,
    get() { window.ctorReads++; return Promise; },
    set(v) {},
  });
</script>
"""

WORKER = (
    "<script>window.w = new Worker(URL.createObjectURL("
    "new Blob(['self.onmessage = e => postMessage(1)'], "
    "{type: 'text/javascript'})));</script>"
)


def _launch_kwargs() -> Dict[str, Any]:
    kwargs: Dict[str, Any] = dict(headless=True, os="linux")
    if EXECUTABLE_PATH:
        kwargs["executable_path"] = EXECUTABLE_PATH
    return kwargs


def _check(results: Dict[str, Any], label: str, got: Any, expected: Any) -> None:
    ok = got == expected
    results[label] = ok
    verdict = "PASS" if ok else "FAIL"
    suffix = "" if ok else f" (expected {expected!r})"
    print(f"  {verdict} {label:42} -> {got!r}{suffix}")


async def _evaluate(page: Any, expression: str) -> Any:
    """Evaluate with a hard timeout, so a hang reports as a hang and not a stall."""
    try:
        return await asyncio.wait_for(page.evaluate(expression), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        return "<hung>"


async def _run() -> bool:
    results: Dict[str, Any] = {}
    async with AsyncCamoufox(**_launch_kwargs()) as browser:
        page = await browser.new_page()
        await page.set_content(PAGE)

        print("-- promise results --")
        _check(results, "sync expression still works", await _evaluate(page, "({ok: 42})"), {"ok": 42})
        _check(results, "async IIFE", await _evaluate(page, "(async () => 42)()"), 42)
        _check(results, "already-resolved promise", await _evaluate(page, "Promise.resolve(7)"), 7)
        _check(
            results,
            "then chain",
            await _evaluate(page, "Promise.resolve(1).then(v => v + 1).then(v => v * 10)"),
            20,
        )
        _check(
            results,
            "timer-settled promise",
            await _evaluate(page, "new Promise(r => setTimeout(() => r('late'), 250))"),
            "late",
        )
        _check(
            results,
            "function returning a promise",
            await _evaluate(page, "async () => { await new Promise(r => setTimeout(r, 50)); return 'fn'; }"),
            "fn",
        )
        _check(
            results,
            "object through a promise",
            await _evaluate(page, "(async () => ({a: [1, 2, {b: 3}]}))()"),
            {"a": [1, 2, {"b": 3}]},
        )

        print()
        print("-- rejection --")
        rejected = "<no error>"
        try:
            await asyncio.wait_for(
                page.evaluate("(async () => { throw new Error('boom'); })()"), timeout=TIMEOUT
            )
        except asyncio.TimeoutError:
            rejected = "<hung>"
        except Exception as exc:  # noqa: BLE001 -- the message is what is under test
            rejected = "boom" if "boom" in str(exc) else str(exc)
        _check(results, "rejection carries its message", rejected, "boom")

        print()
        print("-- concurrency --")
        gathered: Any
        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        page.evaluate(f"new Promise(r => setTimeout(() => r({i}), {30 * i}))")
                        for i in range(6)
                    ]
                ),
                timeout=TIMEOUT,
            )
        except asyncio.TimeoutError:
            gathered = "<hung>"
        _check(results, "six promises awaited at once", gathered, [0, 1, 2, 3, 4, 5])

        print()
        print("-- isolation --")
        # Read the trap counters from the page's own world: the isolated world
        # cannot see them, so have page script write them onto the DOM.
        await page.evaluate(
            """() => {
                const s = document.createElement('script');
                s.textContent =
                    "document.getElementById('out').setAttribute("
                    + "'data-trap', window.thenCalls + '/' + window.ctorReads + '/' + window.thenArgs)";
                document.body.appendChild(s);
            }"""
        )
        _check(
            results,
            "page never sees Promise machinery",
            await page.get_attribute("#out", "data-trap"),
            "0/0/",
        )

        print()
        print("-- workers --")
        async with page.expect_event("worker", timeout=TIMEOUT * 1000) as worker_info:
            await page.set_content(WORKER)
        worker = await worker_info.value
        _check(results, "worker sync expression", await _evaluate(worker, "1 + 1"), 2)
        _check(results, "worker async IIFE", await _evaluate(worker, "(async () => 42)()"), 42)
        _check(
            results,
            "worker timer-settled promise",
            await _evaluate(worker, "new Promise(r => setTimeout(() => r('w'), 50))"),
            "w",
        )

    return all(results.values())


async def main() -> int:
    passed = await _run()
    print()
    if passed:
        print("PASS: Promise-returning evaluate resolves, and stays out of the page's world")
        return 0
    print("FAIL: Promise-returning evaluate is hanging or leaking into the page")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
