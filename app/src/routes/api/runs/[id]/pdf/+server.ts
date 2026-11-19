import { error } from '@sveltejs/kit';
import { renderPdf } from '#lib/server/pdf.js';
import { getRun } from '#lib/server/store.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, url }) => {
	const run = await getRun(params.id);
	if (!run) error(404, 'No such run.');
	if (!run.steps?.length) error(400, 'This run has no steps to export.');
	const pdf = await renderPdf(run);
	const name = run.title.replace(/[^\w\- .]+/g, '_');
	const disposition = url.searchParams.has('inline') ? 'inline' : 'attachment';
	return new Response(new Uint8Array(pdf), {
		headers: {
			'Content-Type': 'application/pdf',
			'Content-Disposition': `${disposition}; filename="${name} - Manual.pdf"`
		}
	});
};
