/**
 * ffprobe / ffmpeg: probe a video, fit it under the serving request cap, cut it
 * into chunks, and grab step frames.
 *
 * A serving query carries the video inline as base64 and the whole request is
 * capped at 2 MiB, so most videos are re-encoded first. MGA samples 64 frames at
 * 224 px, so frame size and rate can drop a long way; the soundtrack must stay,
 * since the speech transcript is half the model's input and sets step boundaries.
 */
import { spawn } from 'node:child_process';
import { mkdir, stat } from 'node:fs/promises';
import path from 'node:path';
import type { Probe } from '#lib/types.js';

type Stream = {
	codec_type: string;
	r_frame_rate?: string;
	width?: number;
	height?: number;
	duration?: string;
};

export const BODY_CAP = 2 * 1024 * 1024;
/** Past this a single input fails; longer videos go in chunks. */
export const SINGLE_PASS_MAX_S = 300;
export const CHUNK_S = 90;
export const OVERLAP_S = 15;
const MIN_CHUNK_FRAMES = 64;

type Rung = [height: number | null, fps: number | null, crf: number, kbps: number];
// Tried in order until the request fits.
const ENCODES: Rung[] = [
	[360, 15, 30, 32],
	[360, 8, 32, 24],
	[240, 5, 32, 24],
	[180, 4, 34, 16],
	[144, 2, 36, 16]
];
// A chunk tries the source's own frame size and rate first.
const CHUNK_ENCODES: Rung[] = [[null, null, 23, 64], ...ENCODES];

export function run(cmd: string, args: string[]): Promise<string> {
	return new Promise((resolve, reject) => {
		const p = spawn(cmd, args);
		let out = '';
		let err = '';
		p.stdout.on('data', (d: Buffer) => (out += d));
		p.stderr.on('data', (d: Buffer) => (err += d));
		p.on('error', (e) => reject(new Error(`${cmd} not found — install ffmpeg (${e.message})`)));
		p.on('close', (code) =>
			code === 0 ? resolve(out) : reject(new Error(`${cmd} exited ${code}: ${err.slice(-400)}`))
		);
	});
}

export async function probe(file: string): Promise<Probe> {
	const j = JSON.parse(
		await run('ffprobe', [
			'-v',
			'error',
			'-print_format',
			'json',
			'-show_format',
			'-show_streams',
			file
		])
	);
	const v = (j.streams as Stream[]).find((s) => s.codec_type === 'video');
	if (!v) throw new Error('No video stream found in this file.');
	const [num, den] = String(v.r_frame_rate ?? '0/1')
		.split('/')
		.map(Number);
	return {
		durationSec: Number(j.format?.duration ?? v.duration ?? 0),
		width: Number(v.width ?? 0),
		height: Number(v.height ?? 0),
		fps: den ? num / den : 0,
		hasAudio: (j.streams as Stream[]).some((s) => s.codec_type === 'audio'),
		sizeBytes: Number(j.format?.size ?? 0)
	};
}

/** Bytes of the JSON query body for a video of `size` bytes. */
export function bodySize(size: number, prompt: string): number {
	const shell = Buffer.byteLength(
		JSON.stringify({ model: 'mga', query: prompt, file_ids: ['base64:'] })
	);
	return shell + 4 * Math.ceil(size / 3);
}

function encodeArgs([height, fps, crf, kbps]: Rung, audio: boolean): string[] {
	const filters = [...(height ? [`scale=-2:${height}`] : []), ...(fps ? [`fps=${fps}`] : [])];
	const a = audio ? ['-c:a', 'aac', '-ac', '1', '-ar', '16000', '-b:a', `${kbps}k`] : ['-an'];
	return [
		...(filters.length ? ['-vf', filters.join(',')] : []),
		'-c:v',
		'libx264',
		'-crf',
		String(crf),
		'-preset',
		'slow',
		'-pix_fmt',
		'yuv420p',
		...a,
		'-movflags',
		'+faststart'
	];
}

const sizeOf = async (f: string) => (await stat(f)).size;

/** Re-encode a whole video until its query fits the cap; null if nothing does. */
export async function fitWhole(
	src: string,
	p: Probe,
	prompt: string,
	outDir: string
): Promise<string | null> {
	if (src.toLowerCase().endsWith('.mp4') && bodySize(p.sizeBytes, prompt) <= BODY_CAP) return src;
	await mkdir(outDir, { recursive: true });
	const out = path.join(outDir, 'whole.mp4');
	for (const rung of ENCODES) {
		await run('ffmpeg', [
			'-nostdin',
			'-v',
			'error',
			'-y',
			'-i',
			src,
			...encodeArgs(rung, p.hasAudio),
			out
		]);
		if (bodySize(await sizeOf(out), prompt) <= BODY_CAP) return out;
	}
	return null;
}

export type Chunk = { index: number; file: string; offset: number; duration: number };

/** Chunk windows of CHUNK_S sharing OVERLAP_S with the next. A short tail joins the chunk before it. */
export function planWindows(duration: number, fps: number): [number, number][] {
	const starts = [0];
	while (starts[starts.length - 1] + CHUNK_S < duration - 0.05)
		starts.push(starts[starts.length - 1] + CHUNK_S - OVERLAP_S);
	const w: [number, number][] = starts.map((t) => [t, Math.min(t + CHUNK_S, duration)]);
	if (w.length > 1) {
		const [a, b] = [w[w.length - 2], w[w.length - 1]];
		if (b[1] - a[1] < OVERLAP_S || (b[1] - b[0]) * fps < MIN_CHUNK_FRAMES + 2)
			w.splice(-2, 2, [a[0], b[1]]);
	}
	return w;
}

/** Cut and re-encode one window, best quality first, until its query fits the cap. */
export async function cutWindow(
	src: string,
	p: Probe,
	start: number,
	length: number,
	prompt: string,
	out: string
): Promise<void> {
	await mkdir(path.dirname(out), { recursive: true });
	for (const rung of CHUNK_ENCODES) {
		if ((rung[1] ?? p.fps ?? 30) * length < MIN_CHUNK_FRAMES + 2) continue;
		await run('ffmpeg', [
			'-nostdin',
			'-v',
			'error',
			'-y',
			'-ss',
			start.toFixed(3),
			'-i',
			src,
			'-t',
			length.toFixed(3),
			...encodeArgs(rung, p.hasAudio),
			out
		]);
		if (bodySize(await sizeOf(out), prompt) <= BODY_CAP) return;
	}
	throw new Error(
		`${start.toFixed(0)}–${(start + length).toFixed(0)} s does not fit the 2 MiB request cap at any quality.`
	);
}

/** Cut each planned chunk. */
export async function cutChunks(
	src: string,
	p: Probe,
	prompt: string,
	outDir: string
): Promise<Chunk[]> {
	const chunks: Chunk[] = [];
	for (const [i, [start, end]] of planWindows(p.durationSec, p.fps || 30).entries()) {
		const out = path.join(outDir, `chunk${String(i).padStart(2, '0')}.mp4`);
		await cutWindow(src, p, start, end - start, prompt, out);
		chunks.push({
			index: i,
			file: out,
			offset: +start.toFixed(3),
			duration: +(end - start).toFixed(3)
		});
	}
	return chunks;
}

/**
 * One JPEG at a timestamp. Jump to just before it by index (fast, keyframe-aligned),
 * then decode forward to the exact frame.
 */
export async function extractFrame(video: string, atSec: number, out: string) {
	await mkdir(path.dirname(out), { recursive: true });
	const target = Math.max(0, atSec);
	const lead = Math.min(2, target);
	await run('ffmpeg', [
		'-nostdin',
		'-v',
		'error',
		'-y',
		'-ss',
		String(target - lead),
		'-i',
		video,
		'-ss',
		String(lead),
		'-frames:v',
		'1',
		'-vf',
		"scale='min(1280,iw)':-2",
		'-q:v',
		'3',
		out
	]);
}
