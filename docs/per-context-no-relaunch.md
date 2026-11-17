# One process, every identity: what per-context still lacks

`per-context-patches.md` lets a context carry its own navigator, screen, WebGL, fonts, voices, timezone and WebRTC IP. A
launcher that wants a **new identity without a relaunch** still has to relaunch for the keys below: a launch draw sets them,
no setter reaches them. This page lists each one, the patch that already reads it from `CAMOU_CONFIG`, and the change that
would make it per-context, following the existing pattern (`RoverfoxStorageManager` key per `userContextId`, a sealed
`window.setXxx` offered once, the getter reading the context value before `MaskConfig`).

## Why

Measured on 159.0-beta.43, 8-vCPU ARM, CPU-bound, a Go launcher driving the browser:

- one launch costs the CPU of ~24 short page runs in a fresh context; launches were 49-66% of the browser CPU when the
  identity was rotated every 20-40 contexts;
- a context with a new identity: ~10 ms to draw + ~1.8 s to open; a launch on the same loaded box: ~34 s;
- with the existing setters, a context reports the drawn values in the page and in a dedicated Worker (user agent,
  platform, oscpu, appVersion, cores, screen, colour depth, window position, timezone, WebGL vendor/renderer and tables,
  fonts, voices, WebRTC IP): 0 mismatches in a parity check.

So the remaining keys are what keeps "rotate the identity" a relaunch.

## Keys a launch draw sets that no context setter reaches

From a launch draw (`navigator`, `screen`, `window`, audio, devices, battery, locale, seeds): an Android persona has 57 keys,
39 without a setter; a Windows desktop 54, 34 without.

| keys | read today by | per-context change |
|---|---|---|
| `screen.availWidth`, `screen.availHeight`, `screen.pixelDepth` | `screen-spoofing.patch` / `fingerprint-injection.patch` (`dom/base/nsScreen.cpp`) | extend `setScreenDimensions` with avail width/height (or `setScreenAvail(w, h)`); `pixelDepth` follows `setScreenColorDepth`. Store `scr_aw_<id>`, `scr_ah_<id>` |
| `window.innerWidth/innerHeight`, `window.outerWidth/outerHeight` | `fingerprint-injection.patch` (`dom/base/nsGlobalWindowInner.cpp`) | `setWindowSize(innerW, innerH, outerW, outerH)`, sealed like `setWindowPosition`; the getters read it before `MaskConfig`. Document whether Playwright's `viewport` is expected to match the inner size (it sizes layout; the getters are what a page reads) |
| `window.devicePixelRatio` | `fingerprint-injection.patch` (`MaskConfig::GetDouble("window.devicePixelRatio")`) | `setDevicePixelRatio(r)`, or document that Playwright's `deviceScaleFactor` is the per-context mechanism and that it makes rendering (not only the getter) run at that ratio |
| `mobile` (RDM, phone-width window) | launch only (`mobile-meta-viewport.patch`, `layout/base/nsLayoutUtils.cpp`) | either per-context mobile mode, or a statement that `hasTouch` + `isMobile` + viewport give a page exactly what a `mobile: true` launch gives (meta viewport, pointer/hover media, touch events) |
| `navigator.maxTouchPoints` | `touchscreen-fingerprint-spoofing.patch` (`dom/base/Navigator.cpp`) | `setNavigatorMaxTouchPoints(n)`; keep it consistent with `hasTouch` |
| `navigator.doNotTrack` (and the `DNT` header) | `privacy-signals-spoofing.patch` (`Navigator.cpp`, `nsHttpChannel.cpp`) | `setDoNotTrack(value)` applied to the getter and the request header of the context |
| `navigator.language`, `navigator.languages`, `locale:all`, `locale:language`, `locale:region` | `locale-spoofing.patch`, `per-context-consistency.patch` | already per-context through the context locale; please confirm in this table that a context locale replaces `locale:all` for `Intl` in the page as well as in workers (on 156 the page kept the launch's ICU locale while a Worker took the context's) |
| `AudioContext:sampleRate`, `AudioContext:outputLatency`, `AudioContext:maxChannelCount` | `audio-context-spoofing.patch` (`dom/media/webaudio/AudioContext.cpp`, `CubebUtils.cpp`) | `setAudioContext(sampleRate, outputLatency, maxChannelCount)`; `AudioContext` resolves the context id from its owner window |
| `mediaDevices:enabled`, `mediaDevices:micros`, `mediaDevices:webcams`, `mediaDevices:speakers` | `media-device-spoofing.patch` (`dom/media/MediaManager.cpp`) | `setMediaDevices(micros, webcams, speakers)`; `enumerateDevices` reads the counts for the calling window's context |
| `battery:charging`, `battery:chargingTime`, `battery:dischargingTime`, `battery:level` | wherever `MaskConfig` serves `battery:*` today | `setBattery(charging, level, chargingTime, dischargingTime)` |
| `window.history.length` | `fingerprint-injection.patch` | `setHistoryLength(n)` for the context's first document (or drop it from the per-context draw: a fresh context has a real history) |
| `audio:seed`, `canvas:aaOffset`, `canvas:aaCapOffset`, `fonts:spacing_seed` | — | the docs say this build has no canvas, audio or glyph-spacing noise. Please state here that these keys are inert. On a pinned persona (fixed UA version, GPU, screen, cores) they are the ONLY keys that differ between two launch draws, so if they are inert a relaunch presents exactly the same device and needs no replacement at all |

Unchanged by design and fine to stay launch-wide: the Firefox build itself, `navigator.buildID`, `productSub`, `appName`,
`appCodeName`, `product`, `cookieEnabled`, `humanize`.

## Shared state a page could use

Please list what contexts of one process share that a page can observe or time, so a launcher knows whether many contexts
of one process can be told apart from separate launches: font cache, shader / GPU process caches, JIT state, HTTP cache
partitioning, storage partitioning, `performance.timeOrigin` / process uptime.

## Key -> mechanism table for other launchers

`generate_context_fingerprint()` (Python, TypeScript) is the reference. A table of every config key with its mechanism
(setter / Playwright context option / launch only / inert) would let a launcher in another language reproduce it exactly.
