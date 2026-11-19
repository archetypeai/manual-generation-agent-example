/**
 * Run records in data/runs.json. A run takes minutes (the first one ~15 while
 * the model loads), so it has to survive a page reload. Writes are serialised.
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { DATA_DIR } from './paths';
import type { Run } from '#lib/types.js';

const FILE = path.join(DATA_DIR, 'runs.json');
const ACTIVE = new Set(['queued', 'preparing', 'waiting', 'generating', 'finishing']);

let lock: Promise<unknown> = Promise.resolve();
function exclusive<T>(fn: () => Promise<T>): Promise<T> {
	const p = lock.then(fn, fn);
	lock = p.catch(() => {});
	return p;
}

let recovered = false;

async function readAll(): Promise<Run[]> {
	const first = !recovered;
	recovered = true;
	let runs: Run[];
	try {
		runs = JSON.parse(await readFile(FILE, 'utf8'));
	} catch {
		return [];
	}
	// Work lives in this process: on the first read, a run still active is from before a restart.
	if (first) {
		let changed = false;
		for (const r of runs)
			if (ACTIVE.has(r.status)) {
				Object.assign(r, {
					status: 'failed',
					phase: 'Interrupted',
					error: 'Interrupted by a server restart. Generate it again.'
				});
				changed = true;
			}
		if (changed) await writeAll(runs);
	}
	return runs;
}

async function writeAll(runs: Run[]) {
	await mkdir(DATA_DIR, { recursive: true });
	await writeFile(FILE, JSON.stringify(runs, null, 2));
}

export const listRuns = () =>
	exclusive(async () => (await readAll()).sort((a, b) => b.createdAt.localeCompare(a.createdAt)));
export const getRun = (id: string) =>
	exclusive(async () => (await readAll()).find((r) => r.id === id) ?? null);

export const saveRun = (run: Run) =>
	exclusive(async () => {
		const runs = await readAll();
		const i = runs.findIndex((r) => r.id === run.id);
		if (i >= 0) runs[i] = run;
		else runs.push(run);
		await writeAll(runs);
		return run;
	});

export const patchRun = (id: string, patch: Partial<Run>) =>
	exclusive(async () => {
		const runs = await readAll();
		const i = runs.findIndex((r) => r.id === id);
		if (i < 0) return null;
		runs[i] = { ...runs[i], ...patch };
		await writeAll(runs);
		return runs[i];
	});

export const deleteRun = (id: string) =>
	exclusive(async () => {
		const runs = await readAll();
		await writeAll(runs.filter((r) => r.id !== id));
	});
