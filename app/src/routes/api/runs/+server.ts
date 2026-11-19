/**
 * POST a video as the raw request body (?name=<filename>); the prompt goes in
 * the x-mga-prompt header, URI-encoded. The run is queued at once.
 */
import { error, json } from '@sveltejs/kit';
import { randomBytes } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline as pipe } from 'node:stream/promises';
import { DEFAULT_PROMPT } from '#lib/server/config.js';
import { requireKey } from '#lib/server/key.js';
import { VIDEO_DIR } from '#lib/server/paths.js';
import { enqueue } from '#lib/server/pipeline.js';
import { listRuns, saveRun } from '#lib/server/store.js';
import { probe, SINGLE_PASS_MAX_S, planWindows } from '#lib/server/video.js';
import { fmt, type Note, type Run } from '#lib/types.js';
import type { RequestHandler } from './$types';

const MAX_CHUNKS = 60;

export const GET: RequestHandler = async () => json(await listRuns());

export const POST: RequestHandler = async ({ request, url }) => {
	const key = requireKey(request);
	const name = (url.searchParams.get('name') ?? 'video.mp4').slice(0, 200);
	const ext = path.extname(name).toLowerCase() || '.mp4';
	if (!['.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'].includes(ext))
		error(400, `Unsupported file type ${ext}.`);
	if (!request.body) error(400, 'No video in the request.');

	const id = `${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)}-${randomBytes(3).toString('hex')}`;
	await mkdir(VIDEO_DIR, { recursive: true });
	const file = path.join(VIDEO_DIR, `${id}${ext}`);
	await pipe(
		Readable.fromWeb(request.body as import('node:stream/web').ReadableStream),
		createWriteStream(file)
	);

	let p;
	try {
		p = await probe(file);
	} catch (e) {
		await rm(file, { force: true });
		error(400, `Could not read this video: ${(e as Error).message}`);
	}
	const warnings: Note[] = [];
	if (p.durationSec > SINGLE_PASS_MAX_S) {
		const n = planWindows(p.durationSec, p.fps || 30).length;
		if (n > MAX_CHUNKS) {
			await rm(file, { force: true });
			error(
				400,
				`${fmt(p.durationSec)} would need ${n} chunks (limit ${MAX_CHUNKS}). Split it into shorter recordings.`
			);
		}
		warnings.push({
			kind: 'info',
			text: `${fmt(p.durationSec)} is past the ~5 minute single-pass limit, so it is sent as ${n} overlapping parts and merged.`
		});
	}
	if (!p.hasAudio)
		warnings.push({
			kind: 'check',
			text: 'No audio track. Step boundaries come from the narration, so a silent video gets evenly spaced steps written largely from the picture.'
		});
	if (p.durationSec < 20)
		warnings.push({ kind: 'info', text: 'Very short video: expect only a step or two.' });

	const prompt =
		decodeURIComponent(request.headers.get('x-mga-prompt') ?? '').trim() || DEFAULT_PROMPT;
	const run: Run = {
		id,
		title: name.replace(/\.[^.]+$/, ''),
		videoName: name,
		videoFile: file,
		probe: p,
		prompt,
		createdAt: new Date().toISOString(),
		status: 'queued',
		phase: 'Queued',
		warnings
	};
	await saveRun(run);
	enqueue(id, key);
	return json(run, { status: 201 });
};
