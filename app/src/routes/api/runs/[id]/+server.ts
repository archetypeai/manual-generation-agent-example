import { error, json } from '@sveltejs/kit';
import { rm } from 'node:fs/promises';
import path from 'node:path';
import { requireKey } from '#lib/server/key.js';
import { FRAME_DIR } from '#lib/server/paths.js';
import { enqueue } from '#lib/server/pipeline.js';
import { deleteRun, getRun, patchRun } from '#lib/server/store.js';
import type { Step } from '#lib/types.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	const run = await getRun(params.id);
	if (!run) error(404, 'No such run.');
	return json(run);
};

/** Edits: a new title, and the steps kept, in order, with their (edited) instruction text. */
export const PATCH: RequestHandler = async ({ params, request }) => {
	const run = await getRun(params.id);
	if (!run) error(404, 'No such run.');
	const body = (await request.json()) as {
		title?: string;
		steps?: { uid: number; instruction: string }[];
	};
	const patch: { title?: string; steps?: Step[] } = {};
	if (typeof body.title === 'string' && body.title.trim())
		patch.title = body.title.trim().slice(0, 200);
	if (Array.isArray(body.steps) && run.steps) {
		const byUid = new Map(run.steps.map((s) => [s.uid, s]));
		patch.steps = body.steps
			.filter((e) => byUid.has(e.uid))
			.map((e, n) => ({
				...byUid.get(e.uid)!,
				instruction: String(e.instruction).trim() || byUid.get(e.uid)!.instruction,
				step: n
			}));
	}
	return json(await patchRun(params.id, patch));
};

/** Generate a failed run again from its stored video. */
export const POST: RequestHandler = async ({ params, request }) => {
	const key = requireKey(request);
	const run = await getRun(params.id);
	if (!run) error(404, 'No such run.');
	if (run.status !== 'failed') error(409, 'Only a failed run can be generated again.');
	const queued = await patchRun(params.id, {
		status: 'queued',
		phase: 'Queued',
		error: undefined,
		chunks: undefined,
		finishedAt: undefined
	});
	enqueue(params.id, key);
	return json(queued);
};

export const DELETE: RequestHandler = async ({ params }) => {
	const run = await getRun(params.id);
	if (!run) error(404, 'No such run.');
	if (!['completed', 'failed'].includes(run.status)) error(409, 'This run is still in progress.');
	await rm(run.videoFile, { force: true });
	await rm(path.join(FRAME_DIR, run.id), { recursive: true, force: true });
	await deleteRun(run.id);
	return new Response(null, { status: 204 });
};
