/**
 * One run: probe → fit or chunk → wait for serving → query → merge → frames.
 * Runs go one at a time: they share one serving session.
 */
import { readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { CHUNK_PREAMBLE } from './config';
import { chunkProblem, gridOf, mergeChunks, type ChunkAnswer } from './merge';
import { FRAME_DIR, WORK_DIR } from './paths';
import { ensureReady, getStatus, queryManual } from './serving';
import { getRun, patchRun } from './store';
import {
	cutChunks,
	cutWindow,
	extractFrame,
	fitWhole,
	OVERLAP_S,
	SINGLE_PASS_MAX_S
} from './video';
import type { MgaStep, Note, Run, Step } from '#lib/types.js';

let chain: Promise<unknown> = Promise.resolve();

export function enqueue(runId: string, key: string) {
	chain = chain.then(() => process(runId, key)).catch(() => {});
}

const PROBLEM = {
	loop: 'repeating itself',
	'no-clock': 'without timestamps',
	grid: 'with evenly spaced (made-up) times'
};

/** Two halves of a window, overlapping by OVERLAP_S. */
function halves(offset: number, duration: number): [number, number][] {
	const h = (duration + OVERLAP_S) / 2;
	return [
		[offset, h],
		[offset + duration - h, h]
	];
}

/** A step's representative frame: its midpoint. Its first frame often still shows the previous action. */
const frameAt = (s: MgaStep) => s.timestamp_start + (s.timestamp_end - s.timestamp_start) * 0.5;

async function process(id: string, key: string) {
	const run = await getRun(id);
	if (!run || run.status !== 'queued') return;
	const update = (patch: Partial<Run>) => patchRun(id, patch);
	const work = path.join(WORK_DIR, id);
	try {
		const p = run.probe!;
		await update({ status: 'preparing', phase: 'Preparing the video' });

		// Fit the whole video under the request cap if it is short enough; chunk otherwise.
		let pieces: { file: string; offset: number; duration: number; prompt: string }[] = [];
		const whole =
			p.durationSec <= SINGLE_PASS_MAX_S
				? await fitWhole(run.videoFile, p, run.prompt, work)
				: null;
		if (whole) pieces = [{ file: whole, offset: 0, duration: p.durationSec, prompt: run.prompt }];
		else {
			await update({ phase: 'Splitting into overlapping chunks' });
			const chunked = await cutChunks(run.videoFile, p, `${CHUNK_PREAMBLE} ${run.prompt}`, work);
			pieces = chunked.map((c) => ({
				...c,
				prompt: c.index ? `${CHUNK_PREAMBLE} ${run.prompt}` : run.prompt
			}));
			await update({
				chunks: chunked.map((c) => ({
					index: c.index,
					offset: c.offset,
					duration: c.duration,
					steps: null
				}))
			});
		}

		const st = await getStatus(key);
		if (st.state !== 'ready') {
			await update({
				status: 'waiting',
				phase: 'Starting the agent — the first load takes about 15 minutes'
			});
			await ensureReady(key);
		}

		const answers: ChunkAnswer[] = [];
		const recut: Note[] = [];
		for (const [i, piece] of pieces.entries()) {
			await update({
				status: 'generating',
				phase:
					pieces.length > 1
						? `Generating chunk ${i + 1} of ${pieces.length}`
						: 'Generating the manual'
			});
			let rows: MgaStep[] | null = null;
			try {
				rows = await queryManual(key, await readFile(piece.file), piece.prompt);
			} catch (e) {
				if (pieces.length === 1) throw e;
			}
			// A chunk that starts mid-procedure can lose the thread. The same input gets the
			// same answer, so send a different one: two shorter halves, once.
			const problem = pieces.length > 1 && rows ? chunkProblem(rows) : null;
			let found = rows?.length ?? null;
			if (problem && (problem !== 'grid' || p.hasAudio)) {
				recut.push({
					kind: 'info',
					start: piece.offset,
					end: piece.offset + piece.duration,
					text: `Came back ${PROBLEM[problem]}, so it was sent again as two shorter halves.`
				});
				await update({
					phase: `Chunk ${i + 1} came back ${PROBLEM[problem]}; sending it as two halves`
				});
				found = 0;
				for (const [k, [o, d]] of halves(piece.offset, piece.duration).entries()) {
					const prompt = o ? `${CHUNK_PREAMBLE} ${run.prompt}` : run.prompt;
					const out = path.join(work, `chunk${String(i).padStart(2, '0')}${'ab'[k]}.mp4`);
					let half: MgaStep[] | null = null;
					try {
						await cutWindow(run.videoFile, p, o, d, prompt, out);
						half = await queryManual(key, await readFile(out), prompt);
					} catch {
						/* this half has no steps; the merge notes it */
					}
					answers.push({ offset: +o.toFixed(3), duration: +d.toFixed(3), rows: half });
					found += half?.length ?? 0;
				}
			} else answers.push({ offset: piece.offset, duration: piece.duration, rows });
			if (pieces.length > 1) {
				const cur = await getRun(id);
				await update({
					chunks: cur!.chunks!.map((c) => (c.index === i ? { ...c, steps: found } : c))
				});
			}
		}

		const warnings = [...run.warnings];
		let raw: MgaStep[];
		if (pieces.length === 1) raw = (answers[0].rows ?? []).map((r) => ({ ...r }));
		else {
			const merged = mergeChunks(answers, p.fps || 30);
			raw = merged.steps;
			warnings.push(...recut, ...merged.notes);
			for (const a of answers.filter((x) => x.rows === null))
				warnings.push({
					kind: 'check',
					start: a.offset,
					end: a.offset + a.duration,
					text: `The agent failed on this part, so only its overlap with the neighbouring parts has steps.`
				});
		}
		if (!raw.length)
			throw new Error(
				'The agent returned an empty manual. Try again, or check the video has a visible procedure.'
			);

		const g = pieces.length === 1 ? gridOf(raw) : null;
		if (g)
			warnings.push({
				kind: 'check',
				text: `${g.all ? 'Every step is' : 'Most steps are'} ${g.length} s long, so the step times are a grid, not a reading of the video. Check them against the footage.`
			});

		await update({ status: 'finishing', phase: 'Extracting step frames', warnings });
		const steps: Step[] = [];
		for (const [n, r] of raw.entries()) {
			const out = path.join(FRAME_DIR, id, `${n}.jpg`);
			let frame: string | undefined;
			try {
				await extractFrame(
					run.videoFile,
					Math.min(frameAt(r), Math.max(0, p.durationSec - 0.1)),
					out
				);
				frame = `${n}.jpg`;
			} catch {
				/* a step without a picture is still a step */
			}
			steps.push({ ...r, step: n, uid: n, frame });
		}
		await update({
			status: 'completed',
			phase: 'Done',
			steps,
			finishedAt: new Date().toISOString()
		});
	} catch (e) {
		const status = (e as { status?: number }).status;
		const message =
			status === 401 || status === 403
				? 'The API key was rejected. Set a valid key and generate again.'
				: (e as Error).message;
		await update({ status: 'failed', phase: 'Failed', error: message });
	} finally {
		await rm(work, { recursive: true, force: true }).catch(() => {});
	}
}
