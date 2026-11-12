/**
 * Port of python/tests/test_audio_architecture.py: an identity claims a
 * machine of the host's CPU architecture, because stock Firefox renders audio
 * one way on x86-64 and another on ARM64 (audio-is-not-noised), and the render
 * is not spoofed.
 */
import * as fs from "node:fs";
import { afterAll, afterEach, describe, expect, it } from "vitest";
import {
	configOf,
	fingerprints,
	HOME,
	restoreDeps,
	SCRATCH,
	stubHost,
	utils,
	warnings,
} from "./launch-host.js";

const {
	APPLE_SILICON_PANELS,
	gpuArchitecture,
	identityArchitecture,
	isAppleSiliconPanel,
} = await import("../src/coherence.js");
const { sampleWebglForScreen } = await import("../src/webgl.js");
await import("./fpgen-setup.js");

const deps = utils.utilsDeps;
const SEEDS = Array.from({ length: 60 }, (_, i) => i);
const WARNING = warnings.loadWarnings().audio_architecture;

afterEach(() => restoreDeps());
afterAll(() => fs.rmSync(SCRATCH, { recursive: true, force: true }));

async function launchOn(
	arch: "arm" | "x86",
	os: string,
): Promise<{ config: Record<string, any>; warned: boolean }> {
	stubHost();
	deps.hostOsKey = () => "lin";
	deps.hostArchitecture = () => arch;
	const {
		result,
		error,
		warnings: caught,
	} = await warnings.recordWarnings(() =>
		utils.launchOptions({ env: { HOME }, headless: true, os }),
	);
	if (error) throw error;
	return {
		config: configOf(result as Record<string, any>),
		warned: caught.some((w) => w.message.includes(WARNING)),
	};
}

describe("the GPU draw follows the host's architecture", () => {
	it("draws only Intel Macs on x86", () => {
		for (const seed of SEEDS) {
			const gpu = sampleWebglForScreen("mac", 1920, 1080, seed, 8, "x86");
			expect(gpuArchitecture(gpu["webGl:vendor"], gpu["webGl:renderer"])).toBe(
				"x86",
			);
		}
	});
	it("draws only Apple Silicon Macs on ARM", () => {
		for (const seed of SEEDS) {
			const gpu = sampleWebglForScreen("mac", 1512, 982, seed, 10, "arm");
			expect(gpu["webGl:renderer"]).toContain("Apple M");
		}
	});
	it("never draws a Raspberry Pi for x86 Linux", () => {
		for (const seed of SEEDS) {
			const gpu = sampleWebglForScreen("lin", 1920, 1080, seed, 8, "x86");
			expect(gpu["webGl:vendor"]).not.toBe("Broadcom");
		}
	});
	it("keeps the recorded GPUs for Windows on ARM", () => {
		const gpu = sampleWebglForScreen("win", 1920, 1080, 1, 8, "arm");
		expect(gpuArchitecture(gpu["webGl:vendor"], gpu["webGl:renderer"])).toBe(
			"x86",
		);
	});
	it("gives an Intel Mac an Intel Mac screen", () => {
		expect(APPLE_SILICON_PANELS.size).toBeGreaterThan(0);
		for (const _ of SEEDS) {
			const fp = fingerprints.generateFingerprint({ os: "macos", arch: "x86" });
			expect(isAppleSiliconPanel(fp.screen.width, fp.screen.height)).toBe(
				false,
			);
		}
	});
});

describe("a launch claims the host's architecture or warns", () => {
	for (const arch of ["x86", "arm"] as const) {
		it(`a Mac on ${arch} matches it`, async () => {
			for (let i = 0; i < 10; i++) {
				const { config, warned } = await launchOn(arch, "macos");
				expect(identityArchitecture(config)).toBe(arch);
				expect(warned).toBe(false);
			}
		});
	}
	it("a phone on x86 warns", async () => {
		expect((await launchOn("x86", "android")).warned).toBe(true);
	});
	it("a phone on ARM does not", async () => {
		expect((await launchOn("arm", "android")).warned).toBe(false);
	});
	it("Windows on ARM warns", async () => {
		expect((await launchOn("arm", "windows")).warned).toBe(true);
	});
	it("Windows on x86 does not", async () => {
		expect((await launchOn("x86", "windows")).warned).toBe(false);
	});
});
