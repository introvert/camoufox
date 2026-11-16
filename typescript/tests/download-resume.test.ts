/**
 * webdl() resumes a download whose connection breaks partway through, as the
 * Python twin does. GitHub's asset hosts sometimes close the connection in the
 * middle of a browser archive. The download continues with a Range request,
 * and only from exactly the bytes it already wrote.
 */
import * as http from "node:http";
import type { AddressInfo } from "node:net";
import { PassThrough } from "node:stream";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const PAYLOAD = Buffer.alloc(256 * 4096, 0).map((_, i) => i % 256);
const BREAK_AT = 300_000;

type Answer = (res: http.ServerResponse, start: number | null) => void;

const servers: http.Server[] = [];

beforeEach(() => {
	vi.resetModules();
});

afterEach(async () => {
	for (const server of servers.splice(0)) {
		server.closeAllConnections();
		await new Promise((resolve) => server.close(resolve));
	}
});

/** A local server answering each GET with `answer`; records every Range start. */
async function serve(answer: Answer): Promise<[string, (number | null)[]]> {
	const ranges: (number | null)[] = [];
	const server = http.createServer((req, res) => {
		const header = req.headers.range;
		const start = header ? Number(header.slice("bytes=".length, -1)) : null;
		ranges.push(start);
		answer(res, start);
	});
	servers.push(server);
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	const { port } = server.address() as AddressInfo;
	return [`http://127.0.0.1:${port}/camoufox.zip`, ranges];
}

/** Announce PAYLOAD[start..], send only PAYLOAD[start..end], then drop the connection. */
function send(
	res: http.ServerResponse,
	status: number,
	start: number,
	end: number,
	partial: boolean,
): void {
	const headers: Record<string, string> = {
		"content-length": String(PAYLOAD.length - start),
	};
	if (partial)
		headers["content-range"] =
			`bytes ${start}-${PAYLOAD.length - 1}/${PAYLOAD.length}`;
	res.writeHead(status, headers);
	if (end >= PAYLOAD.length) {
		res.end(PAYLOAD.subarray(start));
		return;
	}
	res.write(PAYLOAD.subarray(start, end), () => res.socket?.destroy());
}

async function download(url: string): Promise<Buffer> {
	const pkgman = await import("../src/pkgman.js");
	pkgman.RESUME_DELAYS_MS.fill(0);
	const sink = new PassThrough();
	const chunks: Buffer[] = [];
	sink.on("data", (chunk: Buffer) => chunks.push(chunk));
	await pkgman.webdl(url, "x", false, sink, { progressCallback: () => {} });
	return Buffer.concat(chunks);
}

it("a broken connection resumes where it stopped", async () => {
	const [url, ranges] = await serve((res, start) =>
		start === null
			? send(res, 200, 0, BREAK_AT, false)
			: send(res, 206, start, PAYLOAD.length, true),
	);
	expect((await download(url)).equals(PAYLOAD)).toBe(true);
	expect(ranges).toEqual([null, BREAK_AT]);
});

it("each resume continues from the bytes received so far", async () => {
	// Only consecutive breaks that receive nothing end the download.
	const step = Math.floor(PAYLOAD.length / 6);
	const [url, ranges] = await serve((res, start) =>
		send(res, start ? 206 : 200, start ?? 0, (start ?? 0) + step, !!start),
	);
	expect((await download(url)).equals(PAYLOAD)).toBe(true);
	const starts = [];
	for (let at = step; at < PAYLOAD.length; at += step) starts.push(at);
	expect(ranges).toEqual([null, ...starts]);
});

it("a server that ignores the range fails loudly", async () => {
	// A 200 to a Range request is the whole file again: appending it would
	// corrupt the archive.
	const [url] = await serve((res, start) =>
		send(res, 200, 0, start === null ? BREAK_AT : PAYLOAD.length, false),
	);
	await expect(download(url)).rejects.toThrow(
		`Could not resume ${url} at byte ${BREAK_AT}`,
	);
});

it("breaks that receive nothing end the download", async () => {
	const [url, ranges] = await serve((res, start) =>
		start === null
			? send(res, 200, 0, BREAK_AT, false)
			: send(res, 206, start, start, true),
	);
	const { RESUME_DELAYS_MS } = await import("../src/pkgman.js");
	await expect(download(url)).rejects.toThrow();
	expect(ranges).toEqual([
		null,
		...Array(RESUME_DELAYS_MS.length).fill(BREAK_AT),
	]);
});
