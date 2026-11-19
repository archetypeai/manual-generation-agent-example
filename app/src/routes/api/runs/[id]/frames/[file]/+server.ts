import { error } from '@sveltejs/kit';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { FRAME_DIR } from '#lib/server/paths.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	if (!/^\d+\.jpg$/.test(params.file) || !/^[\w-]+$/.test(params.id)) error(400, 'bad frame');
	const img = await readFile(path.join(FRAME_DIR, params.id, params.file)).catch(() => null);
	if (!img) error(404, 'No such frame.');
	return new Response(new Uint8Array(img), {
		headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=3600' }
	});
};
