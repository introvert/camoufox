# Freedom

The web watches. This is a browser for the people who would rather not be
watched.

A fork of [Camoufox](https://github.com/daijro/camoufox): Firefox with the
fingerprint spoofed in C++, below where any page can look. Every launch is a
whole, consistent device that is not yours.

## Get it

Builds for Linux and macOS, x86_64 and arm64, are on the
[releases page](https://github.com/introvert/camoufox/releases).

## Use it

```bash
npm install camoufox playwright-core
```

```javascript
import { Camoufox } from "camoufox";

const browser = await Camoufox({ executable_path: "./camoufox-bin", geoip: true });
const page = await browser.newPage();
await page.goto("https://example.com");
await browser.close();
```

Point `executable_path` at the browser unpacked from a release. On macOS that is
`Camoufox.app/Contents/MacOS/camoufox`. Every option is in
[`typescript/README.md`](typescript/README.md).

## Build it

```bash
cd browser
make dir && make bootstrap && make build
```

Free to use, study, change and share, under the [MPL 2.0](LICENSE).
