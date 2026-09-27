# Android persona: every field, and how to set it

What each field a Firefox for Android integrity check can read looks like on
a real device, and how Camoufox sets it. "Firefox for Android" values come
from the Gecko 155 source this build is made from: `#ifdef ANDROID` branches,
`@IS_ANDROID@` pref defaults in `modules/libpref/init/StaticPrefList.yaml`,
and `mobile/android/app/geckoview-prefs.js`. Fenix (the Firefox for Android
app) layers its own prefs on top, which are not in this tree. Where that could
matter, the row says so, and a real device is the tie-breaker.

`os="android"` sets everything marked **auto**. Every row can also be set by
hand through `config=` (or `CAMOU_CONFIG` for the raw binary), or through
`firefox_user_prefs=` where the row names a pref.

| Field | Firefox for Android | How Camoufox sets it | With `os="android"` |
|---|---|---|---|
| `navigator.userAgent` | `Mozilla/5.0 (Android 16; Mobile; rv:155.0) Gecko/155.0 Firefox/155.0` | `navigator.userAgent`. The HTTP `User-Agent` header follows it (`headers.User-Agent` overrides the header alone) | auto (BrowserForge) |
| `navigator.platform` | `Linux armv81`: hardcoded in `Navigator::GetPlatform`, and yes, the digit **1**, not `armv8l` | `navigator.platform` | auto |
| `navigator.oscpu` | `Linux armv81`: hardcoded in `nsHttpHandler` | `navigator.oscpu` | auto |
| `navigator.appVersion` | `5.0 (Android 16)` | `navigator.appVersion` | auto (derived from the UA) |
| `navigator.buildID` | `20181001000000`: every page outside `https://*.mozilla.org` | `navigator.buildID` | Gecko default is already right |
| `navigator.vendor` | `""` | `navigator.vendor` | Gecko default is already right |
| `navigator.product` / `productSub` | `Gecko` / `20100101` | `navigator.product`, `navigator.productSub` | Gecko default is already right |
| `navigator.appCodeName` / `appName` | `Mozilla` / `Netscape` | `navigator.appCodeName`, `navigator.appName` (workers follow) | Gecko default is already right |
| `navigator.hardwareConcurrency` | the device's cores | `navigator.hardwareConcurrency` | auto |
| `navigator.maxTouchPoints` | `5` on multitouch phones | `navigator.maxTouchPoints`. A `has_touch` context or mobile mode without a count reports 5 | auto (5) |
| `navigator.languages` | the app locales | `navigator.languages` (list), or `locale=`. Both feed `intl.accept_languages`, so `Accept-Language` stays the same list | from `locale` / `geoip` |
| `navigator.pdfViewerEnabled` | `true` unless `pdfjs.disabled`. GeckoView leaves pdf.js on in-tree; **check Fenix on a real device** | `pdfViewerEnabled`. `navigator.plugins` and `mimeTypes` follow it, and the launcher sets `pdfjs.disabled` to match | Gecko default (`true`) |
| `navigator.plugins.length` / `mimeTypes.length` | `5` / `2` with a viewer, `0` / `0` without | follow `pdfViewerEnabled`. `navigator.plugins` (bool) moves them alone, with a warning, since no real Firefox splits them | follow |
| `navigator.userAgentData` | absent (Gecko has no client hints) | nothing to set | absent |
| `navigator.connection` | absent (`dom.netinfo.enabled` is false) | pref `dom.netinfo.enabled` | absent |
| `navigator.vibrate` | absent in Gecko 155 (`dom.vibrator.enabled` is false, GeckoView does not turn it on); check Fenix on a device | pref `dom.vibrator.enabled` | absent |
| `navigator.getBattery` | absent (chrome-only) | nothing to set | absent |
| `navigator.share` | present (`dom.webshare.enabled`, geckoview-prefs.js) | pref `dom.webshare.enabled` | auto |
| `navigator.standalone` | absent (Safari only) | nothing to set | absent |
| `screen.width` × `height` | the device, portrait | `screen.width`, `screen.height` | auto |
| `screen.availWidth` × `availHeight` | the same as the screen | `screen.availWidth`, `screen.availHeight` | auto (no taskbar carved out) |
| `screen.colorDepth` | `24` | `screen.colorDepth` | auto |
| `devicePixelRatio` | the device, e.g. `2.625`, `3` | pref `layout.css.devPixelsPerPx` (real: media queries and canvas agree), or `window.devicePixelRatio` (the JS value only) | auto, when headless or on a virtual display |
| `screen.orientation.type` / `angle` | `portrait-primary` / `0` held upright | `screen.orientation` (`portrait-primary`, `landscape-primary`, ...), applied by Juggler | auto, from the screen's shape |
| `window.orientation` / `onorientationchange` | `0` / present | compiled out of desktop Firefox; `window-orientation.patch` builds it everywhere and exposes it where the page is a phone (`mobile`, or an `is_mobile` context), following `screen.orientation` | auto |
| `(pointer: coarse)`, `(hover: none)` | true, for the primary pointer and the any- set | `mobile`, or a `has_touch` context | auto |
| `'ontouchstart' in window`, `document.createTouch` | true (legacy touch APIs are on only on Android) | `mobile`, or a `has_touch` context | auto |
| `TouchEvent` | `function` | `mobile`, `has_touch`, or `navigator.maxTouchPoints` > 0 | auto |
| `ondevicemotion`, `ondeviceorientation` | present | present on desktop too | present |
| `<meta name="viewport">` | obeyed | `mobile`, or an `is_mobile` context | auto |
| Pages without a meta viewport | laid out 980px wide and zoomed out to fit | `mobile:zoom: true` zooms out like Android. Off by default: Juggler's click and screenshot coordinates do not account for the zoom, so a zoomed-out page can mis-click | 980px wide at 1:1 |
| `navigator.doNotTrack` + `DNT` header | `unspecified`, no header | `navigator.doNotTrack` (`"1"`, `"0"`, `"unspecified"`); the header is sent only for `"1"` | Firefox default |
| `navigator.globalPrivacyControl` + `Sec-GPC` header | `false`, no header, outside private browsing | `navigator.globalPrivacyControl`; window, workers and header move together | Firefox default |
| WebGL vendor / renderer | the device GPU, sanitized, e.g. `Qualcomm` / `Adreno (TM) 650, or similar` | `webGl:vendor` / `webGl:renderer`, or `webgl_config=`. Camoufox has no parameter data for phone GPUs: the strings are set and the rest of WebGL is the host's, with a warning | a Linux desktop GPU, with a warning |
| WebGL default antialias | off (`webgl.default-antialias`) | pref `webgl.default-antialias` | auto |
| `AudioContext().sampleRate` | the device, usually `48000` | `AudioContext:sampleRate` | host |
| `Intl` time zone | the device | `timezone`, or `geoip` | from `geoip` |
| WebCodecs (`VideoEncoder`, ...) | absent (Nightly only on Android) | pref `dom.media.webcodecs.enabled` | auto (off) |
| `HTMLMediaElement.setSinkId` | absent | pref `media.setsinkid.enabled` | auto (off) |
| `documentPictureInPicture` | absent | pref `dom.documentpip.enabled` | auto (off) |
| `navigator.keyboard.lock` | absent | pref `dom.fullscreen.keyboard_lock.enabled` | auto (off) |
| `HTMLInputElement.capture` | present (geckoview-prefs.js) | pref `dom.capture.enabled` | auto |
| `<input type=month/week>` | real pickers | pref `dom.forms.datetime.others` | auto |
| Scrollbar width | 0 (overlay) | pref `ui.useOverlayScrollbars` | auto |
| Fonts, speech voices | Android's (Roboto, Noto, ...) | `fonts`, `voices`. Camoufox bundles no Android fonts | the Linux pools, with a warning |

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
