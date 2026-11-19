import { error, json } from '@sveltejs/kit';
import { keyOf, requireKey } from '#lib/server/key.js';
import { checkKey, getStatus, start, stop } from '#lib/server/serving.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ request }) =>
	json(await getStatus(keyOf(request) ?? undefined));

export const POST: RequestHandler = async ({ request }) => {
	const key = requireKey(request);
	const { action } = await request.json();
	if (action === 'check') {
		const problem = await checkKey(key);
		return json({ ok: !problem, error: problem });
	}
	if (action === 'start') {
		const problem = await checkKey(key);
		if (problem) error(400, problem);
		start(key).catch(() => {});
		return json(await getStatus());
	}
	if (action === 'stop') {
		await stop(key);
		return json(await getStatus());
	}
	error(400, `unknown action ${action}`);
};
