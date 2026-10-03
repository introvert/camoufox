# Patch Dependencies

Quick reference for which patches depend on shared infrastructure.

## camoucfg (MaskConfig)

Most patches read config via `MaskConfig::GetBool()`, `MaskConfig::GetString()`, etc. from `/camoucfg`. Any patch that adds `LOCAL_INCLUDES += ["/camoucfg"]` to a `moz.build` file depends on `config.patch` being applied first (which provides the `camoucfg` directory).

### Patches using MaskConfig

| Patch | Config keys | What it does |
|-------|-------------|--------------|
| `media-codec-spoofing.patch` | `media:spoof_codecs` | Bypasses `PDMFactory::Supports()` checks in `MP4Decoder` and `MatroskaDecoder` so `canPlayType()`/`isTypeSupported()` don't leak system codec libraries |
| `navigator-spoofing.patch` | Various `navigator:*` keys | Per-context navigator property spoofing |
| `geolocation-spoofing.patch` | `geo:*` keys | Geolocation coordinate spoofing |
| `locale-spoofing.patch` | `locale:*` keys, `navigator.languages` | Language/locale spoofing; `navigator.languages` feeds `intl.accept_languages` so the header and the JS list agree |
| `force-default-pointer.patch` | `navigator.maxTouchPoints`, `mobile` | Fixed desktop pointer/hover media features; `mobile` switches both pointer sets to Android's coarse, non-hovering touchscreen |
| `touchscreen-fingerprint-spoofing.patch` | `navigator.maxTouchPoints`, `mobile` | Touch interfaces for a spoofed digitizer; `mobile` adds Android's legacy touch APIs (`ontouchstart`, `createTouch`) and defaults `maxTouchPoints` to 5. A Playwright `hasTouch` context reports the digitizer Juggler gives it |
| `navigator-config-keys.patch` | `navigator.appCodeName`, `appName`, `product`, `productSub`, `vendor`, `buildID`, `cookieEnabled`, `onLine` | Reads the navigator keys that were declared but unread; workers follow for the ones they expose |
| `privacy-signals-spoofing.patch` | `navigator.doNotTrack`, `navigator.globalPrivacyControl` | The window, workers (GPC) and the `DNT` / `Sec-GPC` headers answer from the same config value |
| `window-orientation.patch` | `mobile` | Builds `window.orientation` / `onorientationchange` on desktop and exposes them for a phone (mobile mode or an `isMobile` context); the angle follows the orientation override |
| `pdf-viewer-spoofing.patch` | `pdfViewerEnabled`, `navigator.plugins` | Answers `navigator.pdfViewerEnabled` from the config and makes `navigator.plugins` / `navigator.mimeTypes` follow it, both ways. `navigator.plugins` (bool) moves the two lists alone, with a launcher warning since no real Firefox splits them. The launcher also sets `pdfjs.disabled` to match `pdfViewerEnabled` |
| `mobile-meta-viewport.patch` | `mobile`, `mobile:zoom` | Honours `<meta name="viewport">` in mobile mode, or in RDM for a Playwright `isMobile` context (Juggler sets `forceDesktopViewport` on every other page). Applies on top of `0-playwright.patch`'s disabled meta viewport. Zooming is on for a phone (`mobile:zoom: false` turns it off), so pages without the tag, or wider than the screen, zoom out to fit; Juggler maps input and viewport screenshots to the zoomed screen |
| `webgl-spoofing.patch` | `webGl:*`, `webGl2:*` | Answers only GPU constants (limits, ranges, precision formats, extension list, renderer strings) from the table (`MaskConfig::IsGLConstantParam`); state the page sets, context attributes and extension-gated names stay Firefox's own |
| `network-patches.patch` | `headers.User-Agent`, `headers.Accept-Language`, `headers.Accept-Encoding` | Request headers; `headers.Accept-Encoding` replaces the https list only |

## RoverfoxStorageManager

Per-context patches that use cross-process storage depend on `cross-process-storage.patch`.

## roverfox/

`patches/roverfox/` is applied after every other patch (`scripts/patch.py`), so a
change there can touch files many earlier patches share without reordering them.

| Patch | What it does |
|-------|--------------|
| `roverfox/per-context-consistency.patch` | `window.setWebGLParameters(json)`: a context's whole WebGL record, so its limits and extensions describe the GPU its renderer string names. Worker and service worker requests (no browsing context) send the context's user agent; `navigator.appVersion` follows a per-context user agent in windows and workers; a per-context locale becomes Firefox's language list in `Accept-Language` and `navigator.languages`; OffscreenCanvas text uses its own context's spacing seed |

## Playwright

All patches should be applied after `0-playwright.patch` and `1-leak-fixes.patch`.
