/**
 * Stitch per-chunk answers into one manual (the `dedupe` rule from merge_chunks.py).
 *
 *  1. A chunk is dropped whole when it loops (6+ steps, under 34% distinct) or
 *     when every step has zero length: its clock is lost.
 *  2. A step whose chunk-local time runs past the chunk (+5 s) is dropped.
 *  3. The rest move to video time.
 *  4. Overlap duplicates go: a step matches a kept one only if it came from a
 *     DIFFERENT chunk, starts within 20 s, and either overlaps it in time and
 *     shares 35%+ of its words, or follows it by at most 3 s (two chunks can cut
 *     one action at slightly different times) and shares 60%+. Without overlap,
 *     consecutive actions on one part share ~40% ("Retrieve a band filter
 *     wrench" / "Place the band filter wrench around the oil filter"). Text
 *     alone would merge real repeats ("Click OK" x3).
 */
import type { MgaStep, Note } from '#lib/types.js';

const NEAR_S = 20;
const SIMILAR = 0.35;
const SIMILAR_GAP = 0.6;
const MAYBE = 0.25;
const SLACK_S = 5;
const GAP_S = 3;

type Row = MgaStep & { chunk: number };

const words = (t: string) =>
	new Set(
		t
			.toLowerCase()
			.replace(/[^a-z0-9\s]/g, ' ')
			.split(/\s+/)
			.filter((w) => w.length > 2)
	);

function similarity(a: string, b: string) {
	const x = words(a);
	const y = words(b);
	if (!x.size || !y.size) return 0;
	let inter = 0;
	for (const w of x) if (y.has(w)) inter++;
	return inter / (x.size + y.size - inter);
}

const degenerate = (rows: MgaStep[]) =>
	rows.length >= 6 &&
	new Set(rows.map((r) => r.instruction.trim().toLowerCase())).size / rows.length < 0.34;
const noClock = (rows: MgaStep[]) =>
	rows.length > 0 && rows.every((r) => (r.timestamp_end ?? 0) <= (r.timestamp_start ?? 0));

/**
 * Four or more steps, 75%+ of one length: the times are a grid, not a reading of the
 * video. Returns that length and whether every step has it.
 */
export function gridOf(rows: MgaStep[]): { length: number; all: boolean } | null {
	if (rows.length < 4) return null;
	const counts = new Map<number, number>();
	for (const r of rows) {
		const l = Math.round(r.timestamp_end - r.timestamp_start);
		counts.set(l, (counts.get(l) ?? 0) + 1);
	}
	const [length, n] = [...counts].sort((a, b) => b[1] - a[1])[0];
	return n / rows.length >= 0.75 ? { length, all: n === rows.length } : null;
}
const grid = (rows: MgaStep[]) => gridOf(rows) !== null;

/** Why a chunk's answer can't be trusted as it is, or null. */
export const chunkProblem = (rows: MgaStep[]): 'loop' | 'no-clock' | 'grid' | null =>
	degenerate(rows) ? 'loop' : noClock(rows) ? 'no-clock' : grid(rows) ? 'grid' : null;

export type ChunkAnswer = { offset: number; duration: number; rows: MgaStep[] | null };

export function mergeChunks(
	chunks: ChunkAnswer[],
	fps: number
): { steps: MgaStep[]; notes: Note[] } {
	const notes: Note[] = [];
	let rows: Row[] = [];
	chunks.forEach((c, i) => {
		if (!c.rows) return;
		const where = { start: c.offset, end: c.offset + c.duration };
		if (degenerate(c.rows) || noClock(c.rows))
			return void notes.push({
				kind: 'check',
				...where,
				text: `The agent ${degenerate(c.rows) ? 'repeated itself' : 'returned no timestamps'} here, so this part was dropped. Only its overlap with the neighbouring parts has steps.`
			});
		const g = gridOf(c.rows);
		if (g)
			notes.push({
				kind: 'check',
				...where,
				text: `${g.all ? 'Every step is' : 'Most steps are'} ${g.length} s long, so these times are a grid, not a reading of the video. Check these steps against the footage.`
			});
		for (const r of c.rows) {
			if (r.timestamp_start > c.duration + SLACK_S || r.timestamp_end > c.duration + SLACK_S)
				continue;
			const ts = +(r.timestamp_start + c.offset).toFixed(3);
			const te = +(r.timestamp_end + c.offset).toFixed(3);
			rows.push({
				...r,
				timestamp_start: ts,
				timestamp_end: te,
				frame_start: Math.round(ts * fps),
				frame_end: Math.round(te * fps),
				chunk: i
			});
		}
	});
	rows.sort((a, b) => a.timestamp_start - b.timestamp_start || a.chunk - b.chunk);

	const kept: Row[] = [];
	for (const r of rows) {
		let dup = -1;
		for (let i = kept.length - 1; i >= 0; i--) {
			const k = kept[i];
			if (r.timestamp_start - k.timestamp_start > NEAR_S) break;
			if (k.chunk === r.chunk) continue;
			const overlap =
				Math.min(k.timestamp_end, r.timestamp_end) - Math.max(k.timestamp_start, r.timestamp_start);
			if (overlap < -GAP_S) continue;
			if (similarity(k.instruction, r.instruction) >= (overlap > 0 ? SIMILAR : SIMILAR_GAP)) {
				dup = i;
				break;
			}
		}
		if (dup < 0) kept.push(r);
		else {
			const k = kept[dup];
			const score = (x: Row) => [x.instruction.trim().length, x.timestamp_end - x.timestamp_start];
			const [a1, a2] = score(r);
			const [b1, b2] = score(k);
			if (a1 > b1 || (a1 === b1 && a2 > b2)) kept[dup] = r;
		}
	}
	// Keeping the better of two duplicates can move a step's start: sort again.
	rows = kept.sort((a, b) => a.timestamp_start - b.timestamp_start || a.chunk - b.chunk);

	// Near misses: overlapping steps from different chunks, a little short of the bar.
	for (const [i, a] of rows.entries())
		for (const b of rows.slice(i + 1)) {
			if (b.timestamp_start - a.timestamp_start > NEAR_S) break;
			const overlap =
				Math.min(a.timestamp_end, b.timestamp_end) - Math.max(a.timestamp_start, b.timestamp_start);
			const sim = similarity(a.instruction, b.instruction);
			if (a.chunk !== b.chunk && overlap > 0 && sim >= MAYBE && sim < SIMILAR)
				notes.push({
					kind: 'check',
					start: a.timestamp_start,
					end: Math.max(a.timestamp_end, b.timestamp_end),
					text: `"${a.instruction}" and "${b.instruction}" may describe the same action. Keep one if they do.`
				});
		}
	return {
		steps: rows.map((r, n) => {
			const { chunk, ...step } = r;
			void chunk;
			return { ...step, step: n };
		}),
		notes
	};
}
