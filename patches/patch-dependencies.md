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
| `mobile-meta-viewport.patch` | `mobile`, `mobile:zoom` | Honours `<meta name="viewport">` in mobile mode, or in RDM for a Playwright `isMobile` context (Juggler sets `forceDesktopViewport` on every other page). Applies on top of `0-playwright.patch`'s disabled meta viewport. `mobile:zoom` also allows zooming, so pages without the tag zoom out to fit |

## RoverfoxStorageManager

Per-context patches that use cross-process storage depend on `cross-process-storage.patch`.

## Playwright

All patches should be applied after `0-playwright.patch` and `1-leak-fixes.patch`.
