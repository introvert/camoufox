import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
	FIRST_LAUNCH_TIMEOUT_MS,
	withFirstLaunchTimeout,
} from "../src/utils.js";

const dirs: string[] = [];

function options(): Record<string, any> {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "camoufox-fc-"));
	dirs.push(dir);
	const conf = path.join(dir, "fonts.conf");
	fs.writeFileSync(conf, "<fontconfig/>");
	return {
		env: { FONTCONFIG_FILE: conf, XDG_CACHE_HOME: path.join(dir, "cache") },
	};
}

function writeCache(opts: Record<string, any>, mtimeOffset: number): void {
	const cacheDir = path.join(opts.env.XDG_CACHE_HOME, "fontconfig");
	fs.mkdirSync(cacheDir, { recursive: true });
	const cache = path.join(cacheDir, "abc-le64.cache-9");
	fs.writeFileSync(cache, "");
	const since = fs.statSync(opts.env.FONTCONFIG_FILE).mtimeMs / 1000;
	fs.utimesSync(cache, since + mtimeOffset, since + mtimeOffset);
}

afterEach(() => {
	for (const dir of dirs.splice(0)) fs.rmSync(dir, { recursive: true });
});

describe("withFirstLaunchTimeout", () => {
	it("gives a cold font cache the first-launch timeout", () => {
		const opts = options();
		expect(withFirstLaunchTimeout(opts).timeout).toBe(FIRST_LAUNCH_TIMEOUT_MS);
		expect("timeout" in opts).toBe(false);
	});

	it("treats a cache older than the font configuration as cold", () => {
		const opts = options();
		writeCache(opts, -60);
		expect(withFirstLaunchTimeout(opts).timeout).toBe(FIRST_LAUNCH_TIMEOUT_MS);
	});

	it("keeps Playwright's timeout once the cache is warm", () => {
		const opts = options();
		writeCache(opts, 60);
		expect(withFirstLaunchTimeout(opts)).toBe(opts);
	});

	it("keeps a timeout the caller set", () => {
		const opts = { ...options(), timeout: 1234 };
		expect(withFirstLaunchTimeout(opts).timeout).toBe(1234);
	});

	it("leaves options without a font configuration alone", () => {
		expect("timeout" in withFirstLaunchTimeout({ env: {} })).toBe(false);
	});
});
