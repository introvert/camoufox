# Firefox 155 rebase: what changed and what backs it

Engineering record for the Firefox 155.0.1 rebase and the two defects found on it.
Written so the next person can tell which claims were measured and which were only
reasoned about.

- `main` @ `428708d` — PR #6 merged, so the WebGL work is on `main`
- branch `claude/firefox-155-camoufox-sync-3a69t6` carries the `beta.34` release bump (PR #7)
- last tag is `v155.0.1-beta.33` @ `915bf9f`, which **predates** the WebGL work

## Where each change stands

| Change | State | What backs it |
| --- | --- | --- |
| Promise-returning `evaluate` no longer hangs | Verified | Reproduced before and after on the release binary; 113 tests pass across five suites |
| Failed reaction attach rejects instead of hanging | Verified | Forced the attach to throw: unguarded never returns, guarded errors at once |
| Headless WebGL gets a real context | Verified | Built in PR #6; headless gets a context that renders, pixel-identical to headful |
| WebGL parity test | Verified | Fails correctly on the unpatched build; all 42 identity assertions hold against a real context |
| Rendered WebGL pixels match the spoofed GPU | Not attempted | Measured absent: framebuffer hash identical with and without a spoof config |
| `premultipliedAlpha` spoofing | Verified | Leak reproduced, key corrected, and the probe now returns the config's value |
| Whole-fingerprint grade on the built binary | Verified | `build-tester`, 8 profiles: grade A, 1054/1054, five runs |
| Contexts sharing a proxy keep their own login | Verified | 407s reproduced on the beta.33 release binary; 4/4 clean runs after, 24/24 requests |
| `make tests` failures were the harness, not the code | Verified | 28 failures cut to 8, all 8 reproduced on the release binary |
| Concurrent contexts stall on uBlock Origin, not on Juggler | Verified | MOZ_LOG shows the channel suspended by WebRequest; 0 stalls in 5 runs without the addon |

## Shipped: the evaluate hang

Firefox 155 removed `Debugger.prototype.onPromiseSettled`. Juggler awaited promise
results by assigning that hook, and on a `Debugger` object the assignment is not an
error — it silently creates an ordinary property that nothing ever calls. Every
Promise-returning evaluate parked forever with no error, no timeout and no protocol
traffic.

```js
page.evaluate("({ok: 42})")          // returned
page.evaluate("(async () => 42)()")  // hung until the caller gave up
```

`Runtime._awaitPromise()` now attaches reactions inside the debuggee that run a
`debugger;` statement on settlement, and sweeps the pending set from
`onDebuggerStatement`, which 155 still has. The hook carries no argument naming the
promise, so the sweep resolves every entry that is no longer pending; a `debugger;`
the page runs itself lands there too and makes the sweep a no-op.

Because `page.evaluate` runs in Camoufox's isolated world, those reactions use the
sandbox's own `Promise`. A page hooking `Promise.prototype.then` and trapping
`Promise.prototype.constructor` recorded zero calls and zero reads.

A second commit rejects instead of hanging when the attach itself fails, which can
happen through the `mw:` hatch or under `disableWorldIsolation`, where `then` is
whatever page script left on the prototype.

| Suite | Before | After |
| --- | --- | --- |
| `tests/patches/promise-evaluate.py` (new) | 9 hangs | 15 pass |
| `tests/async/test_page_evaluate.py` | 1 hang, 34 pass | 35 pass |
| `test_worker.py`, `test_jshandle.py`, `test_add_init_script.py` | not run | 33 pass |
| four evaluate patch tests | not run | all pass |

## On the branch: headless WebGL

In native headless `canvas.getContext('webgl')` returns `null`, so the browser
claims a GPU in every other signal and has none. All the WebGL spoofing is bypassed,
because it lives in `ClientWebGLContext` and only ever rewrites a context that was
created.

The cause is not a missing driver. Firefox takes its answer from gfxInfo, gfxInfo
takes its answer from the glxtest probe, and that probe opens an X connection before
anything else:

```
$ env -u DISPLAY ./gfxtest glx -f 1
ERROR
Unable to open a connection to the X server

$ xvfb-run ./gfxtest glx -f 1
DRI_DRIVER swrast / VENDOR Mesa / RENDERER llvmpipe (LLVM 20.1.2, 256 bits)
```

With the probe failed, `GfxInfo` blocks most features by default, the X11 EGL
feature is force-disabled because the probe never reported EGL, `UseEGL` stays
false, and `GLContextProviderLinux::CreateHeadless()` selects GLX for a display that
does not exist.

### Three things ruled out by measurement

- **The DRI drivers were never missing.** `libgl1-mesa-dri` was installed with the
  base image and present for every failing test. Necessary for the fix, not the
  cause of the fault.
- **Prefs cannot reach it.** The X11 EGL force-disable outranks
  `gfx.x11-egl.force-enabled`; the `gfx.blacklist.*` overrides do not move the
  gating vars in headless; and `webgl.force-enabled` bypasses the blocklist but
  clears `FORBID_HARDWARE`, the only other route into the EGL provider.
- **A complete Mesa stack changes nothing on its own.** With EGL, DRI, swrast and
  the blocklist bypassed, headless still ends at `Exhausted GL driver options`.

### The destination works

Driving surfaceless EGL by hand with `DISPLAY` unset on a box with no `/dev/dri`,
running the exact pipeline WebGL needs — offscreen framebuffer, compiled program,
draw, readback:

```
renderer: llvmpipe (LLVM 20.1.2, 256 bits)
framebuffer complete: True
shaders compile: True      program links: True
centre pixel: [51, 178, 102, 255]   corner pixel: [0, 0, 0, 255]
```

Those centre bytes are the fragment shader's `vec4(0.2, 0.7, 0.4, 1.0)`. A WebGL
canvas never presents to a window, so no display is needed; Xvfb was only ever
satisfying GLX's need for something to talk to.

### What the patch became

Four review passes, four defects, each found by reading rather than running:

1. The first version flipped `gfxVars::UseEGL()` in headless. That variable is read
   by six subsystems.
2. The second fixed a `FeatureState` ordering bug — force-enabling after `Disable()`
   trips an assertion in debug builds — and the DMABUF fallout from the flip.
3. The third abandoned the flip. The fix now sits in
   `GLContextProviderLinux::CreateHeadless`, the offscreen factory WebGL actually
   calls, leaving DMABUF, hardware video, the render compositor and the window code
   untouched. In headless the branch it replaces could only ever fail, so nothing
   working is being redirected.
4. The fourth guarded the `gfxPlatform.cpp` hunk with `MOZ_WIDGET_GTK`. It was
   applying on macOS too, where gfxInfo reaches a verdict without any window system,
   and forcing `WebglUseHardware` false there would drop a working hardware context
   onto a software rasterizer. CI builds macOS.

### The `premultipliedAlpha` leak

`webgl-spoofing.patch` looked the attribute up under
`webGl:contextAttributes.premultipliedAlpha` while already inside the
`webGl:contextAttributes` map, so the key could never match and the host's real
value passed through. Demonstrated on the release build with a config saying
`premultipliedAlpha: false` and a page asking for `true`:

```
reported: {"premultipliedAlpha": true, "antialias": false}
```

`antialias` follows the config; `premultipliedAlpha` returns what the page asked
for. The key is corrected. Only the two added lines changed and their count is
unchanged, so every hunk header stays valid, and the whole patch was re-applied to
pristine sources to confirm it still lands and yields the fixed line. Confirming the
behaviour needs a build, since this is C++.

### Stealth probing of the spoof layer

Adversarial checks against a real context, with an NVIDIA profile pinned, looking
for the inconsistencies a detector cross-references rather than the values it reads
first.

- **Every advertised extension resolves.** All 28 names in `getSupportedExtensions()`
  return a non-null object from `getExtension()`, and extensions llvmpipe has but the
  profile hides stay hidden. A spoofed list over a driver that lacks those extensions
  would be one call to catch; this one holds.
- **The shader error text does not name the backend.** A deliberately broken shader
  returns `ERROR: 0:1: 'this' : Illegal use of reserved word`, which is the
  translator Firefox ships on every platform, not a Mesa message.
- **The shader translator does name its target.** `WEBGL_debug_shaders`, exposed to
  content by default, returns translated source beginning `#version 450` on the GLX
  path. Headless takes the EGL path, and the surfaceless context this work reaches is
  OpenGL ES 3.2, so the header may read as ESSL there instead. That is the one place
  the two paths could visibly diverge, and it is unmeasured until the patch is built.
  The build answered it: both modes report `#version 450`, so there is no
  divergence. The parity test keeps asserting it so a future rebase cannot
  reintroduce one.

## Shipped: contexts sharing one proxy refused each other's logins

A proxy pool usually hands out one gateway `host:port` and tells sessions apart by
the username. Giving six contexts that proxy with six different usernames refused
requests at random, on valid credentials:

```
ctx0 http://example.com/ 200
ctx3 http://example.com/ 407      # valid credentials, still refused
```

Per-context proxies themselves were never broken. Juggler routes each channel
through the proxy belonging to the channel's `userContextId`, and every request
reached the right proxy. What is not per-context is where Firefox keeps the
*password*: `nsHttpAuthCache` keys proxy entries by `host:port` alone and
deliberately leaves the origin-attributes suffix off, because isolating it "would
only annoy users with authentication dialogs popping up" — the right call for a
person behind one corporate proxy, the wrong one when each context is a separate
paid session.

Juggler compensated by wiping the whole auth cache: on every request through a
proxy whose credentials clashed with another context's, and again on every
`setProxy`. Sequentially that works. Concurrently the wipe lands inside another
context's handshake, between the entry being stored and the header being built from
it, so the retry goes out with no `Proxy-Authorization`. The second 407 sets
`PREVIOUS_FAILED`, which `promptAuth` answers by declining, and the 407 reaches the
page.

`proxy-auth-isolation.patch` gives each context its own entry instead, keying proxy
credentials by `userContextId` and `privateBrowsingId` — not the full network state
suffix, whose partition key would force a fresh 407 round trip per top-level site.
The default context produces an empty suffix, which is stock behaviour. With the
entries separated, the wipes go: `NetworkObserver` no longer clears on each request,
`setProxy` clears only when it replaces a proxy the context already had, and the
clash-detection bookkeeping that fed both is gone.

Connections needed nothing: `nsHttpConnectionInfo::BuildHashKey` already appends the
origin-attributes suffix, so two contexts never shared a proxy connection or an
authenticated CONNECT tunnel to begin with.

| Suite | Before | After |
| --- | --- | --- |
| `tests/patches/proxy-auth-isolation.py` (new) | 4-14 of 24 requests refused, every run | 24/24, four runs |
| HTTP, HTTP+auth and SOCKS5, launch-level and per-context | pass | pass |

One thing this measured that the patch does not fix: with six contexts starting at
once, some pages stop navigating entirely and every `goto` times out. It reproduces
with no proxy configured at all, on the beta.33 release and on the official 152
build, so it predates this work. It is uBlock Origin — see below — and the test now
launches without it.

## Shipped: what the 28 `make tests` failures actually were

A full `make tests` on the built binary reported 28 failures. None of them were
the browser.

**Twenty-one were the Playwright version.** `tests/local-requirements.txt` asks for
`playwright` unpinned, so a fresh venv takes the newest release, while
`pythonlib/pyproject.toml` caps the package at `<1.63` exactly because each
Playwright minor may change Juggler. 1.63 does: `Browser.setHTTPCredentials` now
carries an array, so a context can hold a credential per origin, and this fork's
schema rejected it outright.

```
Expected "<root>.credentials.username" to be |string|; found |undefined| `undefined` instead.
```

Every context built with `http_credentials` died there. The requirement is now
pinned to the same ceiling as the package, so the suite measures a pairing the
package will actually install.

**Seven were the missing display.** `async/test_headful.py` opens real windows
whatever `--headless` says. `run-tests.sh` now supplies a virtual one through
`xvfb-run` when `DISPLAY` is unset, which is the normal case on a build box.

**Eight remain, and all eight predate this work.** Seven reproduce on the
`v155.0.1-beta.33` release binary run the same way; the eighth,
`test_frame_goto_should_continue_after_client_redirect`, is flaky — three passes
out of three on the built binary against two failures out of three on the release
one, so it is if anything better here.

| Run | Failures |
| --- | --- |
| `make tests` as it stood | 28 |
| with the Playwright ceiling honoured | 21 fewer |
| with a display for the headful tests | 7 fewer |
| what is left | 8, every one pre-existing |

### Juggler now takes either credentials shape

The array is cheap to accept, and refusing it strands the fork a release behind, so
`Browser.setHTTPCredentials` now takes both: the schema picks its check from what
arrived, `BrowserHandler` stores a list either way, and `promptAuth` picks the first
entry whose origin matches the channel, an entry without an origin answering for
any. The twelve auth and credentials tests fail 11 of 12 on 1.63 before, and pass
all twelve on both 1.62 and 1.63 after.

The ceiling stays at `<1.63` regardless. Credentials was the first 1.63 break to
surface, not the last: a full suite on 1.63 with the fix still fails three tests
that pass on 1.62 — `test_assertions`'s two custom-timeout cases and
`test_should_collect_trace_with_resources_but_no_js`. Those want measuring before
the package claims the version.

## Found while measuring: concurrent contexts stall on uBlock Origin

Six contexts created while others load, and the last two or three never navigate at
all: `goto` times out on every URL, though the page answers `evaluate` and stays on
`about:blank`. It reproduces on the beta.33 release and on the official 152 build,
with no proxy configured, so it is neither new nor proxy-related.

It is not Juggler. `Page.navigate` returns a navigation id and
`Page.navigationStarted` fires; the channel is created, clears Juggler's proxy
filter and reaches `http-on-modify-request`. Then nothing — the server never
accepts a connection and `navigationCommitted` never arrives.

`MOZ_LOG=nsHttp:5` names the culprit:

```
nsHttpChannel::Suspend [this=7c515f270b00]
  called from script: resource://gre/modules/WebRequest.sys.mjs:999:19
  started suspend timer, will fire in 5000ms
...  +5.0s  nsHttpChannel::OnSuspendTimeout          # fires, does not resume
...  +12.7s nsHttpChannel::Cancel status=804b0002    # the test's own timeout
...  +13.1s nsHttpChannel::ResumeInternal
             called from script: resource://gre/modules/WebRequest.sys.mjs:1148:15
```

A blocking `webRequest` listener suspends the channel and does not answer for
thirteen seconds — in the end only as the page is torn down. The listener is
uBlock Origin's: excluding the addon gives five runs with no stall at all, against
stalls in most runs with it, and every earlier raw-Playwright run that never stalled
was one launched without addons.

Playwright hands each launch a fresh profile, so uBO reloads its filter lists every
time, and pages opened during that window are the ones that hang. What it means for
callers is that a fleet opening many contexts at once should pass
`exclude_addons=[DefaultAddons.UBO]` until this is fixed upstream or worked around
in the launcher; the proxy regression test now does exactly that.

## Cost, measured

| Mode | Context | Browser | X server | Total |
| --- | --- | --- | --- | --- |
| native headless today | none | 1020 MB | 0 | 1020 MB |
| headful under Xvfb | yes | 1228 MB | 82 MB | 1311 MB |

Headless EGL should land below the second row: it drops the X server and the X11
compositing while keeping the Mesa cost. No GPU, no passthrough, no device mapping.
The rasterizer is llvmpipe on the CPU, so a page running a heavy WebGL scene pays
for it in processor time.

## How to test it

### 1. Runtime dependencies, Linux only

`libxul` dlopens `libEGL.so.1` rather than linking it, so without these the browser
starts normally and only WebGL fails.

```sh
# Debian, Ubuntu
apt-get install -y libegl1 libegl-mesa0 libgl1-mesa-dri

# Fedora, RHEL, Amazon Linux
dnf install -y mesa-libEGL mesa-dri-drivers
```

### 2. Register a build for the patch tests

The tests under `tests/patches/` launch through `AsyncCamoufox`, which reads the
installed version even when `executable_path` is set. Without one they fail with
`CamoufoxNotInstalled` before reaching any assertion. Run `camoufox fetch` once,
then point everything at the binary you want to exercise.

```sh
export CAMOUFOX_EXECUTABLE_PATH=/path/to/camoufox-bin
```

### 3. The three regression tests

```sh
python tests/patches/promise-evaluate.py
python tests/patches/webgl-headless-parity.py
python tests/patches/proxy-auth-isolation.py
```

The first covers settled, microtask-settled and timer-settled promises, rejection
messages, six concurrent awaits, worker evaluation, and that the page never sees the
automation's Promise machinery. The second pins one GPU and asserts headless gets a
context that compiles, links, draws and reads back the shader's colour, and reports
that GPU rather than the host's. It needs no display; where Xvfb happens to exist it
also compares against a virtual one, and says which it did.
The third runs six contexts through one in-process proxy on six logins, creating
contexts while others load, and asserts no 407 reaches a page and no request carries
another context's login. It needs no network.

### 4. The conformance suites

```sh
cd tests
python -m pytest async/test_page_evaluate.py async/test_worker.py \
  async/test_jshandle.py -q --headless --timeout=60
```

Add `isolated-evaluate.py`, `main-world-eval.py`, `force-scope-access.py` and
`main-world-init-script.py` from `tests/patches/` when touching the juggler layer;
they cover the isolation property the fork exists for. `config-overrides.py` reaches
`api.github.com` and will fail on a sandboxed network for reasons unrelated to the
code.

### 5. Testing a juggler change without rebuilding

Anything under `additions/juggler/` is JavaScript inside `omni.ja`, which is an
ordinary zip. Swapping a file in takes seconds against the forty minutes a build
costs, and Playwright starts each run on a fresh profile so the startup cache never
goes stale. Keep a copy of the original first.

```sh
mkdir -p /tmp/omni/chrome/juggler/content/content
cp additions/juggler/content/Runtime.js \
   /tmp/omni/chrome/juggler/content/content/Runtime.js
cd /tmp/omni && zip -qr0XD /path/to/dist/bin/omni.ja \
   chrome/juggler/content/content/Runtime.js
```

This does not work for the WebGL patch, which is C++ and needs a real build.

### 6. Building the WebGL patch

A `workflow_dispatch` on the branch compiles all four targets and uploads artifacts
without cutting a release, since the release job only fires for tag refs. Locally,
`make dir && make build`, which wants roughly 30 GB free and takes about forty
minutes cold. Then run the parity test against the result.

### 7. A thirty-second smoke test

```python
import asyncio, os
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.firefox.launch(
            executable_path=os.environ["CAMOUFOX_EXECUTABLE_PATH"],
            headless=True, args=["-headless"])
        pg = await b.new_page()
        print("async evaluate:",
              await asyncio.wait_for(pg.evaluate("(async () => 42)()"), 15))
        print("webgl context:", await pg.evaluate(
            "!!document.createElement('canvas').getContext('webgl')"))
        await b.close()

asyncio.run(main())
```

On the current release build this prints `42` and `False`. With the WebGL patch
built it should print `42` and `True`; anything else means the Mesa packages above
are missing.

### 8. The release gate

`build-tester` scores a binary the way CONTRIBUTING requires. It needs no display
and no GPU; it launches the binary itself, so point it at the unpacked build:

```sh
cd build-tester
npm install                        # first run only, builds the checks bundle
pip install -r requirements.txt
python scripts/run_tests.py /path/to/camoufox-bin --save-cert cert.txt
```

Expect grade A at 1054/1054. A score of 1053 with a single
`headlessDetection.noSwiftShader` failure on a Linux profile is the 3% software
preset described below, not a regression — confirm by checking that the same
profile's `webgl.renderer` match result is `True`. The run takes about a minute;
the exit code is non-zero on any failed check.

## Controlling the GPU fleet-wide

The fingerprint config cannot come from a file. The browser reads it as chunked
`CAMOU_CONFIG_<n>` environment variables that `launch_options()` writes fresh on
every launch, so a `CAMOU_CONFIG` exported in userdata is ignored. `camoufox.cfg`
is Firefox prefs, not fingerprint config, despite the name.

So the knob is an environment variable read by the launcher:

```sh
export CAMOUFOX_WEBGL_CONFIG="Mesa|llvmpipe, or similar"
```

Precedence runs: an explicit `webgl_config=` argument, then a preset that names its
own GPU, then this default, then the screen-coherent sampler. A default should lose
to anything chosen deliberately; an operator who wants the fleet policy to beat a
preset passes the argument instead.

Everything else about the fingerprint is untouched. Verified by generating a config
twice with the knob and twice without: the keys that differ between knob and no-knob
are exactly the keys that differ between any two runs, which are the seeds, fonts,
screen and navigator values the generator randomises anyway.

An explicit argument replaces a preset's GPU rather than landing on top of it.
`merge_into()` leaves keys the config already holds, so without that the preset kept
its vendor and renderer strings while the pinned parameter table landed underneath:
`getParameter(RENDERER)` said Intel while `UNMASKED_RENDERER_WEBGL` said llvmpipe, on
the same context. One property read to catch, and the test now guards it.

Pinning skips `sample_webgl_for_screen()`, which is what normally keeps the GPU
coherent with the screen BrowserForge picked, so the two checks it would have made
now run on the pinned value and warn rather than resample. The caller named that
GPU on purpose; quietly substituting another would be worse than saying the pairing
is odd.

- A discrete GPU behind a screen too small for one warns. That gap was real: before
  this, pinning could put a GeForce GTX 980 behind a 1024x600 panel with nothing
  complaining.
- A software rasterizer warns, because `sample_webgl_for_screen()`'s own reasoning
  says it is the strongest VM and headless signal a page can read, and pinning
  applies it to every session instead of the small share of real machines that
  report it.

That second warning is deliberate friction. Pinning llvmpipe buys coherence between
the renderer string and the pixels, and pays for it with a string many vendors
blocklist outright. Neither choice is free, which is why this is a knob and not a
new default.

`tests/patches/webgl-config-knob.py` covers all of it and launches no browser.

## The whole-fingerprint grade, and the one thing it flags

`build-tester` is the suite CONTRIBUTING gates releases on (score >= 1000). It had
never been run on this work. Against the built `beta.33` binary, headless, with no
GPU and no Xvfb, over five runs:

```
OVERALL: [A]  1054/1054 checks passed  (8 profiles)

WebGL Render ............................... 8/8  [PASS]
Canvas Noise ............................... 8/8  [PASS]
Headless Detection ....................... 80/80  [PASS]
Lie Detection ............................ 104/104 [PASS]
```

`WebGL Render 8/8` is the headless fix seen from the page side by the project's own
suite rather than by a test written alongside the patch. Before it, headless had no
context for those checks to score.

Two of the five runs scored 1053/1054 instead, each time a single
`headlessDetection.noSwiftShader` failure on a different Linux per-context profile.
It is not a leak. The suite's own match results for that same profile:

```
webgl.vendor      True   actual=Mesa
webgl.renderer    True   actual=llvmpipe, or similar
```

The profile's assigned fingerprint *was* `Mesa` / `llvmpipe` and the browser
reported it faithfully. `fingerprint-presets-v150.json` is real scraped data, and
real Linux desktops do run software rendering:

| Pool | Presets | Software renderer |
| --- | --- | --- |
| linux | 65 | 2 (3.1%) |
| windows | 180 | 0 |
| macos | 67 | 0 |

So roughly 3% of Linux draws present `llvmpipe`, and `build-tester` flags any
`llvmpipe` as a headless indicator regardless of where it came from. The suite is
being conservative, not catching us.

**It is still worth knowing for a fleet.** An antibot using the same heuristic would
flag that 3%, and a scraping fleet has no reason to opt into a renderer that trips a
cheap check. The mitigation already exists and needs no code change — pin the GPU:

```sh
export CAMOUFOX_WEBGL_CONFIG='NVIDIA Corporation|NVIDIA GeForce GTX 980, or similar'
```

Left as data rather than a fix: filtering the two presets out would mean editing
scraped data to make a heuristic happy, and it removes a genuine configuration that
some operators may want.

## What is still open

**The gate is passed.** PR #6 built the branch on Linux and macOS and every suite
ran against its artifact. What the build settled, beyond compiling:

- Headless gets a real context that compiles, links, draws and reads back the
  shader's colour, with no X server and no GPU.
- `getTranslatedShaderSource` reports `#version 450` in **both** modes, so the EGL
  path does not expose a different shader target from GLX. That was the open stealth
  divergence and it does not exist.
- The framebuffer hash is identical headless and headful, `e0c10396`. Moving a fleet
  off Xvfb does not move the canvas fingerprint.

- **Rendered pixels are not spoofed.** The framebuffer hash was byte-identical with
  and without a spoof config, so the image still describes llvmpipe. This gap exists
  identically in the Xvfb setup today. Closing it means seeded noise at the readback
  boundary, with real breakage risk for genuine WebGL pages.
- **Hunk placement on Firefox 152.** The headless patch applies to upstream's tree
  but its second hunk needs fuzz 2. I tried to re-anchor it on
  `SetWebglUseHardware`, which both versions carry, and it does not work: at normal
  context the hunk fails on 152 outright, and only reaches fuzz 1 with one-line
  context, which thins the anchor for our own tree. The two versions differ in the
  lines immediately after every viable insertion point in that function, so a single
  hunk cannot match both exactly. Left as it is; upstreaming would want the block
  split or the patch cut against 152 directly.
- **Never exercised here.** Live scraping, and the arm64 binary on arm64 hardware.

## Upstream

Daijro's tree pins Firefox 152.0.4 and carries no patch touching any of these files;
nothing in it mentions `UseEGL`, `CreateHeadless`, `glxtest` or `MOZ_HEADLESS`.
Pulling the two files from the `FIREFOX_152_0_4_RELEASE` tag, `CreateHeadless` is
the same function with the same GLX branch. Released Camoufox has this fault on
Linux, and carried the `premultipliedAlpha` leak too until the fix below.
