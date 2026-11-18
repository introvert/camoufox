/**
 * A context identity is data: drawn once, stored, replayed, and changed one
 * value at a time. Twin of python/tests/test_context_identity.py.
 */
import { describe, expect, it } from "vitest";
import {
	type ContextIdentity,
	drawContextIdentity,
	renderContextIdentity,
} from "../src/fingerprints.js";
import { machineFromProbe } from "../src/utils.js";

const { MODEL } = await import("./fpgen-setup.js");
const { NewContext, NewIdentity } = await import("../src/sync_api.js");

const roundtrip = (identity: ContextIdentity): ContextIdentity =>
	JSON.parse(JSON.stringify(identity));

function fakeBrowser(extra: Record<string, any> = {}) {
	const calls: { options?: any; script?: string } = {};
	const browser = {
		version: () => "159.0",
		_camoufoxMachine: {},
		newContext: async (options: any) => {
			calls.options = options;
			return {
				addInitScript: async (script: string) => {
					calls.script = script;
				},
			};
		},
		...extra,
	};
	return { browser: browser as any, calls };
}

describe.skipIf(!MODEL.ok)("context identities", () => {
	it.each([
		"windows",
		"macos",
		"linux",
	])("a stored %s identity replays exactly", (os) => {
		const identity = drawContextIdentity(null, { os, ff_version: "159" });
		const again = drawContextIdentity(roundtrip(identity), {
			ff_version: "159",
		});
		expect(again).toEqual(identity);
		expect(renderContextIdentity(again, "159")).toEqual(
			renderContextIdentity(identity, "159"),
		);
	});

	it("an identity is plain JSON", () => {
		// This GPU's WebGL2 tables hold a value above 2**53.
		const identity = drawContextIdentity(null, {
			os: "linux",
			webgl_config: ["Intel", "Intel(R) HD Graphics 400, or similar"],
		});
		const stored = roundtrip(identity);
		expect(stored).toEqual(identity);
		expect(renderContextIdentity(stored, "159")).toEqual(
			renderContextIdentity(identity, "159"),
		);
	});

	it("new identities differ", () => {
		const a = drawContextIdentity(null, { os: "windows" });
		const b = drawContextIdentity(null, { os: "windows" });
		expect(a.salt).not.toBe(b.salt);
	});

	it("an override keeps everything else", () => {
		const identity = drawContextIdentity(null, { os: "windows" });
		const changed = drawContextIdentity(identity, {
			overrides: { timezone: "Asia/Tokyo" },
			i_know_what_im_doing: true,
		});
		expect(changed.config.timezone).toBe("Asia/Tokyo");
		const { timezone: _a, ...rest } = changed.config;
		const { timezone: _b, ...before } = identity.config;
		expect(rest).toEqual(before);
	});

	it("another machine draws its own values from the salt", () => {
		const identity = drawContextIdentity(null, { os: "windows" });
		const overrides = {
			"navigator.userAgent":
				"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:159.0) Gecko/20100101 Firefox/159.0",
			"navigator.platform": "MacIntel",
		};
		const opts = { overrides, i_know_what_im_doing: true };
		const first = drawContextIdentity(identity, opts);
		expect(drawContextIdentity(identity, opts)).toEqual(first);
		expect(first.config["navigator.oscpu"]).toBe("Intel Mac OS X 10.15");
		expect(first.config.fonts).not.toEqual(identity.config.fonts);
	});

	it("another locale draws the voices again", () => {
		const identity = drawContextIdentity(null, {
			os: "windows",
			locale: "en-US",
		});
		const german = drawContextIdentity(identity, { locale: "de-DE" }).config;
		expect(
			german.voices.some((v: any) => v.name.includes("German (Germany)")),
		).toBe(true);
		expect(german.fonts).toEqual(identity.config.fonts);
	});

	it.each([
		"navigator.doNotTrack",
		"headers.Accept-Encoding",
		"navigator.globalPrivacyControl",
	])("a browser-wide override of %s throws", (key) => {
		expect(() =>
			drawContextIdentity(null, { os: "windows", overrides: { [key]: 1 } }),
		).toThrow(/cannot differ between contexts/);
	});

	it("a core count the browser does not have throws", () => {
		expect(() =>
			drawContextIdentity(null, {
				os: "windows",
				hardware_concurrency: 8,
				overrides: { "navigator.hardwareConcurrency": 4 },
			}),
		).toThrow(/runs on 8 cores/);
	});

	it.each([
		{
			"navigator.userAgent":
				"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0",
		},
		{ "navigator.platform": "Win32" },
	])("an override of one half of the OS throws", (overrides) => {
		const identity = drawContextIdentity(null, { os: "macos" });
		expect(() =>
			drawContextIdentity(identity, { overrides, i_know_what_im_doing: true }),
		).toThrow(/navigator.userAgent .* navigator.platform/);
	});

	it("a context renders at its own pixel ratio", () => {
		const identity = drawContextIdentity(null, {
			os: "windows",
			overrides: { "window.devicePixelRatio": 1.5 },
			i_know_what_im_doing: true,
		});
		expect(
			renderContextIdentity(identity, "159").context_options.deviceScaleFactor,
		).toBe(1.5);
	});

	it("a phone launch keeps its pixel ratio", () => {
		expect(() =>
			drawContextIdentity(null, {
				os: "android",
				device_pixel_ratio: 60 / 21,
				overrides: { "window.devicePixelRatio": 3 },
				i_know_what_im_doing: true,
			}),
		).toThrow(/renders at 2.857/);
	});

	it("a stored identity needs its own cores", () => {
		const identity = drawContextIdentity(null, {
			os: "windows",
			hardware_concurrency: 8,
		});
		expect(() =>
			drawContextIdentity(identity, { hardware_concurrency: 4 }),
		).toThrow(/runs on 4 cores/);
	});

	it.each([
		["fonts", "Arial"],
		["navigator.hardwareConcurrency", 8.5],
	])("an override of %s of another type throws", (key, value) => {
		expect(() =>
			drawContextIdentity(null, {
				os: "windows",
				overrides: { [key]: value },
				i_know_what_im_doing: true,
			}),
		).toThrow(new RegExp(key));
	});

	it("both WebRTC addresses are set", () => {
		const identity = drawContextIdentity(null, {
			os: "linux",
			overrides: { "webrtc:ipv4": "203.0.113.7", "webrtc:ipv6": "2001:db8::7" },
			i_know_what_im_doing: true,
		});
		const script = renderContextIdentity(identity, "159").init_script;
		expect(script).toContain('setWebRTCIPv4("203.0.113.7")');
		expect(script).toContain('setWebRTCIPv6("2001:db8::7")');
	});

	it("NewContext reopens its identity", async () => {
		const { browser, calls } = fakeBrowser();
		const first = await NewContext(browser, {
			os: "linux",
			locale: "fr-FR",
			timezoneId: "Europe/Paris",
		});
		const options = calls.options;
		const script = calls.script;
		await NewContext(browser, {
			identity: roundtrip(first.camoufoxIdentity),
			storageState: "state.json",
		});
		expect(calls.script).toBe(script);
		expect(calls.options).toEqual({ ...options, storageState: "state.json" });
		expect(options.timezoneId).toBe("Europe/Paris");
		expect(options.locale).toBe("fr-FR");
	});

	it("a bound browser refuses an override of its OS", async () => {
		const { browser } = fakeBrowser({ _camoufoxHostIdentity: ["lin", null] });
		await expect(
			NewIdentity(browser, {
				overrides: {
					"navigator.userAgent":
						"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0",
					"navigator.platform": "Win32",
				},
				i_know_what_im_doing: true,
			}),
		).rejects.toThrow(/canvas_noise/);
	});

	it("a browser it did not launch is asked once", async () => {
		const facts = {
			userAgent:
				"Mozilla/5.0 (X11; Linux x86_64; rv:159.0) Gecko/20100101 Firefox/159.0",
			cores: 6,
			devicePixelRatio: 1,
			covered: false,
			gpu: ["Intel", "Intel(R) HD Graphics, or similar"],
		};
		let probes = 0;
		let script = "";
		const browser: any = {
			version: () => "159.0",
			newContext: async () => ({
				newPage: async () => ({
					evaluate: async () => {
						probes++;
						return facts;
					},
				}),
				close: async () => undefined,
				addInitScript: async (s: string) => {
					script = s;
				},
			}),
		};
		await Promise.all([NewContext(browser), NewContext(browser)]);
		await NewContext(browser);
		expect(probes).toBe(1);
		expect(script).toContain("setNavigatorHardwareConcurrency(6)");
		expect(script).toContain("Intel(R) HD Graphics, or similar");
	});
});

describe("machineFromProbe", () => {
	const facts = {
		userAgent:
			"Mozilla/5.0 (X11; Linux x86_64; rv:159.0) Gecko/20100101 Firefox/159.0",
		cores: 6,
		devicePixelRatio: 1,
		covered: false,
		gpu: ["Intel", "Intel(R) HD Graphics, or similar"] as [string, string],
	};

	it("binds an uncovered browser's contexts to its host", () => {
		expect(machineFromProbe(facts)).toEqual([
			["lin", ["Intel", "Intel(R) HD Graphics, or similar"]],
			{ hardware_concurrency: 6, device_pixel_ratio: null },
		]);
	});

	it("binds a software renderer's OS only", () => {
		expect(
			machineFromProbe({ ...facts, gpu: ["Mesa", "llvmpipe, or similar"] })[0],
		).toEqual(["lin", null]);
	});

	it("binds nothing on a covered browser", () => {
		expect(machineFromProbe({ ...facts, covered: true })[0]).toBeNull();
	});
});
