# Android persona: every field, and how to set it

What each field a Firefox for Android integrity check can read looks like on
a real device, and how Camoufox sets it. "Firefox for Android" values come
from the Gecko 155 source this build is made from: `#ifdef ANDROID` branches,
`@IS_ANDROID@` pref defaults in `modules/libpref/init/StaticPrefList.yaml`,
and `mobile/android/app/geckoview-prefs.js`. Fenix (the Firefox for Android
app) layers its own prefs on top, which are not in this tree. Where that could
matter, the row says so, and a real device is the tie-breaker.

`os="android"` sets everything marked **auto**. fpgen records no Firefox for
Android, so the device itself (screen, window, device pixel ratio, user agent,
platform) is drawn from `python/src/camoufox/android-devices.json`: the Firefox
for Android devices BrowserForge recorded, weighted by how often each was seen. Every row can also be set by
hand through `config=` (or `CAMOU_CONFIG` for the raw binary), or through
`firefox_user_prefs=` where the row names a pref.

| Field | Firefox for Android | How Camoufox sets it | With `os="android"` |
|---|---|---|---|
| `navigator.userAgent` | `Mozilla/5.0 (Android 16; Mobile; rv:155.0) Gecko/155.0 Firefox/155.0` | `navigator.userAgent`. The HTTP `User-Agent` header follows it (`headers.User-Agent` overrides the header alone) | auto (`android-devices.json`) |
| `navigator.platform` | `Linux armv81`: hardcoded in `Navigator::GetPlatform`, and yes, the digit **1**, not `armv8l` | `navigator.platform` | auto |
| `navigator.oscpu` | `Linux armv81`: hardcoded in `nsHttpHandler` | `navigator.oscpu` | auto |
| `navigator.appVersion` | `5.0 (Android 16)` | `navigator.appVersion` | auto (derived from the UA) |
| `navigator.buildID` | `20181001000000`: every page outside `https://*.mozilla.org` | `navigator.buildID` | Gecko default is already right |
| `navigator.vendor` | `""` | `navigator.vendor` | Gecko default is already right |
| `navigator.product` / `productSub` | `Gecko` / `20100101` | `navigator.product`, `navigator.productSub` | Gecko default is already right |
| `navigator.appCodeName` / `appName` | `Mozilla` / `Netscape` | `navigator.appCodeName`, `navigator.appName` (workers follow) | Gecko default is already right |
| `navigator.hardwareConcurrency` | the device's cores | `navigator.hardwareConcurrency` | the host's core count, as for every identity; with `pin_cpu_cores=True`, the recorded phone's 8 where the host has that many |
| `navigator.maxTouchPoints` | `5` on multitouch phones | `navigator.maxTouchPoints`. A `has_touch` context or mobile mode without a count reports 5 | auto (5) |
| `navigator.languages` | the app locales | `navigator.languages` (list), or `locale=`. Both feed `intl.accept_languages`, so `Accept-Language` stays the same list | from `locale` / `geoip` |
| `navigator.pdfViewerEnabled` | `true` unless `pdfjs.disabled`. GeckoView leaves pdf.js on in-tree; **check Fenix on a real device** | `pdfViewerEnabled`. `navigator.plugins` and `mimeTypes` follow it, and the launcher sets `pdfjs.disabled` to match (policies.json no longer locks that pref) | Gecko default (`true`) |
| `navigator.plugins.length` / `mimeTypes.length` | `5` / `2` with a viewer, `0` / `0` without | follow `pdfViewerEnabled`. `navigator.plugins` (bool) moves them alone, with a warning, since no real Firefox splits them | follow |
| `navigator.userAgentData` | absent (Gecko has no client hints) | nothing to set | absent |
| `navigator.connection` | absent (`dom.netinfo.enabled` is false) | pref `dom.netinfo.enabled` | absent |
| `navigator.vibrate` | absent in Gecko 155 (`dom.vibrator.enabled` is false, GeckoView does not turn it on); check Fenix on a device | pref `dom.vibrator.enabled` | absent |
| `navigator.getBattery` | absent (chrome-only) | nothing to set | absent |
| `navigator.share` | present (`dom.webshare.enabled`, geckoview-prefs.js) | pref `dom.webshare.enabled` | auto |
| `navigator.requestMIDIAccess` | absent (`dom.webmidi.enabled` is `@IS_NOT_MOBILE@`) | pref `dom.webmidi.enabled` | auto (off) |
| `navigator.mediaDevices.enumerateDevices()` | one microphone, `Default audio input device` (cubeb cannot enumerate on Android, `CubebDeviceEnumerator.cpp`), and the cameras as `Facing front:1`, `Facing back:0`, front first (`VideoCaptureDeviceInfoAndroid.java`); no `audiooutput` (`media.setsinkid.enabled` is off) | `mediaDevices:*` | auto (`android` in `media-devices.json`) |
| `navigator.standalone` | absent (Safari only) | nothing to set | absent |
| `screen.width` × `height` | the device, portrait | `screen.width`, `screen.height` | auto (`android-devices.json`) |
| `screen.availWidth` × `availHeight` | the same as the screen | `screen.availWidth`, `screen.availHeight` | auto (no taskbar carved out) |
| `outerWidth`, and `innerWidth` under `width=device-width` | the screen's width | `window.outerWidth`. Mobile mode lifts desktop Firefox's 500px minimum window width (`--window-min-width` in browser-shared.css) | auto |
| `screen.colorDepth` | `24` | `screen.colorDepth` | auto |
| `devicePixelRatio` | the device, e.g. `2.625`, `3` | pref `layout.css.devPixelsPerPx` (real: media queries and canvas agree), or `window.devicePixelRatio` (the JS value only) | auto, when headless or on a virtual display |
| `screen.orientation.type` / `angle` | `portrait-primary` / `0` held upright | `screen.orientation` (`portrait-primary`, `landscape-primary`, ...), applied by Juggler | auto, from the screen's shape |
| `screen.mozOrientation` | the same as `screen.orientation.type` | follows the orientation override; `window-orientation.patch` | auto |
| `window.orientation` / `onorientationchange` | `0` / present | compiled out of desktop Firefox; `window-orientation.patch` builds it everywhere and exposes it where the page is a phone (`mobile`, or an `is_mobile` context), following `screen.orientation` | auto |
| `(pointer: coarse)`, `(hover: none)` | true, for the primary pointer and the any- set | `mobile`, or a `has_touch` context | auto |
| `'ontouchstart' in window`, `document.createTouch` | true (legacy touch APIs are on only on Android) | `mobile`, or a `has_touch` context | auto |
| `TouchEvent` | `function` | `mobile`, `has_touch`, or `navigator.maxTouchPoints` > 0 | auto |
| Playwright `page.tap()`, `page.touchscreen` | n/a | `has_touch` on the context | auto: pages of a mobile-mode browser open with `has_touch` |
| `ondevicemotion`, `ondeviceorientation` | present | present on desktop too | present |
| `<meta name="viewport">` | obeyed | `mobile`, or an `is_mobile` context | auto |
| Pages without a meta viewport, or wider than the screen | laid out 980px wide and zoomed out to fit | zoom is on in mobile mode; Juggler maps clicks and viewport screenshots to the zoomed screen. `mobile:zoom: false` turns it off | zoomed out, like Android |
| `navigator.doNotTrack` + `DNT` header | `unspecified`, no header | `navigator.doNotTrack` (`"1"`, `"0"`, `"unspecified"`); the header is sent only for `"1"` | Firefox default |
| `navigator.globalPrivacyControl` + `Sec-GPC` header | `false`, no header, outside private browsing | `navigator.globalPrivacyControl`; window, workers and header move together | Firefox default |
| WebGL vendor / renderer | the device GPU, sanitized (`SanitizeRenderer.cpp`), e.g. `Qualcomm` / `Adreno (TM) 650, or similar` | `webGl:vendor` / `webGl:renderer`, or `webgl_config=`. fpgen records no phone GPUs: the strings are set and the rest of WebGL is the host's. A pair outside `android-devices.json` warns | auto: a recorded phone GPU from `android-devices.json` by weight (Adreno 650, Mali-T760, PowerVR SGX 540), with the canvas placeholder, as for any GPU not the host's |
| WebGL default antialias | off (`webgl.default-antialias`) | pref `webgl.default-antialias` | auto |
| `AudioContext().sampleRate` | the device, usually `48000` | `AudioContext:sampleRate` | host |
| `Intl` time zone | the device | `timezone`, or `geoip` | from `geoip` |
| WebCodecs (`VideoEncoder`, ...) | absent (Nightly only on Android) | pref `dom.media.webcodecs.enabled` | auto (off) |
| `HTMLMediaElement.setSinkId` | absent | pref `media.setsinkid.enabled` | auto (off) |
| `documentPictureInPicture` | absent | pref `dom.documentpip.enabled` | auto (off) |
| `navigator.keyboard.lock` | absent | pref `dom.fullscreen.keyboard_lock.enabled` | auto (off) |
| `navigator.serial` | absent (`dom.webserial.enabled` is `@IS_NOT_MOBILE@`) | pref `dom.webserial.enabled` | auto (off) |
| `navigator.mediaDevices.getDisplayMedia` | absent (`media.getdisplaymedia.enabled` is `@IS_NOT_MOBILE@`) | pref `media.getdisplaymedia.enabled` | auto (off) |
| System colors (`Highlight`, `AccentColor`, `-moz-dialog`, ...) | `widget/android/nsLookAndFeel.cpp`: `AccentColor` is the app theme's `colorAccent`, Fenix's `#180e30` (`novaVioletDesaturated90`, light theme); `Highlight` is that accent at alpha 78; `HighlightText` is the text's own color; `-moz-dialog` `#efebe7`. Button, field and the deprecated colors are Gecko's stand-ins on every platform | `ui.*` color prefs (`.dark` for the dark scheme, from `GenericDarkColor`); `ui.highlighttext` is `rgba(2, 0, 0, 0)`, the value of `NS_SAME_AS_FOREGROUND_COLOR`, which style resolves to `currentcolor` | auto |
| `HTMLInputElement.capture` | present (geckoview-prefs.js) | pref `dom.capture.enabled` | auto |
| `<input type=month/week>` | real pickers | pref `dom.forms.datetime.others` | auto |
| `window.open(url, name, "width=300,height=300")` | a new tab, the phone's size (GeckoView has no popup windows) | pref `browser.link.open_newwindow.restriction` (`0`: every new window is a tab) | auto |
| `window.resizeTo` / `moveTo` on a popup | no-ops (`dom.disable_window_move_resize`, geckoview-prefs.js) | pref `dom.disable_window_move_resize` | auto |
| `Cross-Origin-Embedder-Policy: credentialless` | not supported on release builds, so it does not make a page `crossOriginIsolated` (`browser.tabs.remote.coep.credentialless` is Nightly-only on Android) | pref `browser.tabs.remote.coep.credentialless` | auto (off) |
| Scrollbar width | 0 (overlay) | pref `ui.useOverlayScrollbars` | auto |
| Fonts | Android's (Roboto, Noto, Noto Color Emoji, ...) | `fonts` | Android's set as far as the Linux bundle has it (`androidFonts` in `launcher-constants.json`); Droid Sans Mono, Cutive Mono, Coming Soon, Dancing Script and Carrois Gothic SC are missing. DejaVu Sans Mono backs `monospace` (0.602em advance, Droid Sans Mono's is 0.600em): it follows Droid Sans Mono in each `font.name-list.monospace.*`, because a list whose families are all missing falls back to the proportional default font |
| Default font, generic families | sans-serif, Roboto; serif Noto Serif; monospace 12px | prefs `font.default.*`, `font.name-list.*`, `font.size.monospace.*` (Firefox for Android's `all.js`) | `mobileFontPrefs` in `launcher-constants.json` |
| `system-ui`, CSS2 system fonts (`font: caption`, ...) | Roboto, 12px | `navigator.platform` `Linux armv81` (`SpoofedOS::Android`) | Roboto, 12px |
| Speech voices | Android's | `voices` | the Linux pool, with a warning |

## Known wrong values in hand-built personas

- `navigator.platform` / `oscpu` of `Linux armv8l` (letter L) does not exist
  on Firefox for Android: Gecko hardcodes `Linux armv81` (digit one) on every
  Android device.
- A `vibrate` that exists is not what Gecko 155 ships on Android either, unless
  Fenix turns `dom.vibrator.enabled` on, which a device check settles.
- `pdfViewerEnabled: true` with five plugins is Gecko's in-tree Android
  default. If a real device reports `false` and `0`, set
  `config={"pdfViewerEnabled": False}`: the flag, both lists and the viewer
  itself move together.

## What still reads as desktop

These need data recorded on real phones, or browser work, before they can be
set; until then they are the host's:

- **WebGL beyond the two strings.** Limits, extensions and shader precision are
  the host GPU's, so a desktop list (with `WEBGL_compressed_texture_s3tc`) sits
  behind a phone renderer. Adreno and Mali parameter sets have to be recorded.
- **Speech voices.** `urn:moz-tts:speechd:` voices from the Linux pool, where
  Firefox for Android lists the system TTS voices (`urn:moz-tts:android:`).
- **Motion sensors.** `devicemotion` and `deviceorientation` never fire; a
  phone fires them continuously without a permission prompt. Feeding them
  needs a sensor backend in the browser.
- **The `DejaVu Sans Mono` family name.** It resolves by name, which Android
  cannot do. Droid Sans Mono has to be added to the font bundle first.
- **The window chrome.** `outerHeight - innerHeight` is the desktop toolbars'
  height, and `100svh`, `100lvh` and `100dvh` are equal; Fenix's address bar is
  a dynamic toolbar, so `lvh` and `svh` differ by its height. The heights have
  to be recorded on a device.
- **The soft keyboard.** Focusing an input changes neither `innerHeight` nor
  `visualViewport`, and typed text arrives as physical key events
  (`code: "KeyA"`).
- **Input from the mouse API.** `page.click()`, `page.hover()` and
  `page.mouse` send mouse events (`pointerType: "mouse"`), and scrolling sends
  `wheel` events; only `page.tap()` is touch. A tap lands on the element's
  exact center with a 1ms contact and a fixed radius and pressure.
- **`PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()`**
  answers `false`, where a phone with a screen lock answers `true`.
- **EME and HLS.** Firefox for Android has Widevine through MediaDrm
  (`media.eme.enabled` is on there) and plays HLS (`media.hls.enabled`).
  Neither backend exists in a desktop build, so turning the prefs on changes
  only the error a page sees: EME stays off and `canPlayType` for HLS is `""`.
- **The device pool.** `android-devices.json` has six recorded devices, all
  with the user agent's `Android 16`. Gecko sends the device's own major
  version (10 and up), so more recorded devices widen the spread.
