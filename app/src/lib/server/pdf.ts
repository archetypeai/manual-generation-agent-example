/**
 * Render a manual to PDF: a title page, then ONE STEP PER PAGE — a header bar
 * "Step N  [MM:SS–MM:SS]", the step's frame, and the instruction under it.
 * One step per page so a technician following it on paper never has a step's
 * picture and text split across a page break.
 */
import PDFDocument from 'pdfkit';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { FRAME_DIR } from './paths';
import type { Run } from '#lib/types.js';

const MARGIN = 50;
const INK = '#18181b';
const MUTED = '#71717a';
const BAR = '#18181b';

const clock = (s: number) =>
	`${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

export async function renderPdf(run: Run): Promise<Buffer> {
	const steps = run.steps ?? [];
	const doc = new PDFDocument({
		size: 'A4',
		margin: MARGIN,
		bufferPages: true,
		info: { Title: run.title, Creator: 'Archetype AI Manual Generation' }
	});
	const chunks: Buffer[] = [];
	doc.on('data', (c: Buffer) => chunks.push(c));
	const done = new Promise<Buffer>((res) => doc.on('end', () => res(Buffer.concat(chunks))));
	const W = doc.page.width - MARGIN * 2;
	const H = doc.page.height;

	// Title page
	doc
		.font('Helvetica-Bold')
		.fontSize(14)
		.fillColor(INK)
		.text('Archetype AI', MARGIN, 90, { width: W, align: 'center' });
	doc
		.font('Helvetica')
		.fontSize(28)
		.fillColor(INK)
		.text(run.title, MARGIN, 150, { width: W, align: 'center' });
	doc
		.moveDown(0.4)
		.fontSize(14)
		.fillColor(MUTED)
		.text('Procedure Manual', { width: W, align: 'center' });
	doc
		.moveDown(2)
		.fontSize(10.5)
		.fillColor(INK)
		.text(
			`${steps.length} steps generated from "${run.videoName}"` +
				(run.probe ? ` (${clock(run.probe.durationSec)})` : '') +
				' by the Archetype AI Manual Generation Agent. Each step is timestamped to the source video.',
			{ width: W, align: 'center' }
		);
	doc
		.moveDown(0.6)
		.fontSize(9.5)
		.fillColor(MUTED)
		.text(`Generated ${new Date(run.finishedAt ?? run.createdAt).toLocaleDateString()}`, {
			width: W,
			align: 'center'
		});
	doc
		.moveDown(3)
		.fontSize(9)
		.fillColor(MUTED)
		.text('Review every step against the video before relying on it.', {
			width: W,
			align: 'center'
		});

	for (const [i, s] of steps.entries()) {
		doc.addPage();
		const barY = MARGIN;
		doc.rect(MARGIN, barY, W, 34).fill(BAR);
		doc
			.font('Helvetica-Bold')
			.fontSize(13)
			.fillColor('#ffffff')
			.text(`Step ${i + 1}`, MARGIN + 16, barY + 11, { lineBreak: false });
		doc
			.font('Helvetica')
			.fontSize(11)
			.text(`${clock(s.timestamp_start)} – ${clock(s.timestamp_end)}`, MARGIN, barY + 12, {
				width: W - 16,
				align: 'right',
				lineBreak: false
			});

		let y = barY + 34 + 18;
		const img = s.frame
			? await readFile(path.join(FRAME_DIR, run.id, s.frame)).catch(() => null)
			: null;
		if (img) {
			const aspect = (run.probe?.height || 9) / (run.probe?.width || 16);
			let iw = W;
			let ih = iw * aspect;
			const maxH = H - y - MARGIN - 140;
			if (ih > maxH) {
				ih = maxH;
				iw = ih / aspect;
			}
			try {
				doc.image(img, MARGIN + (W - iw) / 2, y, { width: iw, height: ih });
				y += ih + 20;
			} catch {
				/* a corrupt frame must not abort the export */
			}
		}
		doc
			.font('Helvetica')
			.fontSize(12.5)
			.fillColor(INK)
			.text(s.instruction, MARGIN, y, { width: W, lineGap: 3 });
	}

	// Footers need the final page count.
	const range = doc.bufferedPageRange();
	for (let i = range.start; i < range.start + range.count; i++) {
		doc.switchToPage(i);
		const keep = doc.page.margins.bottom;
		doc.page.margins.bottom = 0; // writing below the margin would add a page
		doc
			.font('Helvetica')
			.fontSize(8)
			.fillColor(MUTED)
			.text(`${run.title}  ·  Page ${i + 1} of ${range.count}`, MARGIN, H - MARGIN + 10, {
				width: W,
				align: 'center',
				lineBreak: false
			});
		doc.page.margins.bottom = keep;
	}
	doc.end();
	return done;
}
