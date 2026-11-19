/** The uploaded video, with Range support so the player can seek. */
import { error } from '@sveltejs/kit';
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import path from 'node:path';
import { Readable } from 'node:stream';
import { getRun } from '#lib/server/store.js';
import type { RequestHandler } from './$types';

const TYPES: Record<string, string> = {
	'.mp4': 'video/mp4',
	'.m4v': 'video/mp4',
	'.mov': 'video/quicktime',
	'.webm': 'video/webm',
	'.mkv': 'video/x-matroska',
	'.avi': 'video/x-msvideo'
};

export const GET: RequestHandler = async ({ params, request }) => {
	const run = await getRun(params.id);
	if (!run) error(404, 'No such run.');
	const size = (await stat(run.videoFile).catch(() => null))?.size;
	if (!size) error(404, 'The video file is gone.');
	const type = TYPES[path.extname(run.videoFile).toLowerCase()] ?? 'application/octet-stream';
	const range = /bytes=(\d*)-(\d*)/.exec(request.headers.get('range') ?? '');
	if (!range) {
		return new Response(Readable.toWeb(createReadStream(run.videoFile)) as ReadableStream, {
			headers: { 'Content-Type': type, 'Content-Length': String(size), 'Accept-Ranges': 'bytes' }
		});
	}
	const start = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]));
	const end = range[1] && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
	if (start >= size || start > end)
		return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
	return new Response(
		Readable.toWeb(createReadStream(run.videoFile, { start, end })) as ReadableStream,
		{
			status: 206,
			headers: {
				'Content-Type': type,
				'Content-Length': String(end - start + 1),
				'Content-Range': `bytes ${start}-${end}/${size}`,
				'Accept-Ranges': 'bytes'
			}
		}
	);
};
