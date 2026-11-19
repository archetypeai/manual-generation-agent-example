/**
 * MGA in serving: load the model once, then answer one /query per video.
 *
 * For this app's setting it finds or makes, in order (ids kept in data/serving-<host>.json):
 *
 *   project     "Manual Generation App"   found by name
 *   copy        mga-f64-t16384-pc647bb    `mga` with max_frames / max_new_tokens / prompt set
 *   package     of that copy              its models resolved once
 *   deployment  kind: serving             placed on a GPU worker
 *   session     on the deployment         `running` once the model is loaded (~15 min)
 *
 * The deployment holds a GPU worker until it is stopped, so stop() ends both.
 * A session with no query for 30 minutes is closed by the platform; the next
 * query notices (409) and opens a new session on the same deployment.
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { Atai, ApiError, type Json } from './atai';
import { endpoint, MGA_VALUES, PROJECT_NAME } from './config';
import { DATA_DIR } from './paths';
import type { MgaStep, ServingStatus } from '#lib/types.js';

/** One state file per endpoint: ids from one deployment of the platform mean nothing on another. */
const stateFile = () =>
	path.join(DATA_DIR, `serving-${new URL(endpoint()).host.replace(/[^a-z0-9.-]/gi, '-')}.json`);
const SESSION_TERMINAL = new Set(['completed', 'failed', 'cancelled']);
const LOAD_TIMEOUT_MS = 45 * 60_000;
const QUERY_TIMEOUT_MS = 150_000; // the platform's own budget is ~58 s
const QUERY_RETRIES = 3;

type State = {
	endpoint: string;
	project?: string;
	blueprint?: string;
	package?: string;
	deployment?: string;
	session?: string;
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function load(): State {
	try {
		const s = JSON.parse(readFileSync(stateFile(), 'utf8')) as State;
		if (s.endpoint === endpoint()) return s;
	} catch {
		/* first run */
	}
	return { endpoint: endpoint() };
}

function save(s: State) {
	mkdirSync(DATA_DIR, { recursive: true });
	writeFileSync(stateFile(), JSON.stringify(s, null, 2));
}

/** `mga-f64-t16384-pc647bb`: the copy's key, the prompt as the first 6 hex of its md5. */
export const settingKey = () =>
	`mga-f${MGA_VALUES.max_frames}-t${MGA_VALUES.max_new_tokens}-p${createHash('md5').update(MGA_VALUES.prompt).digest('hex').slice(0, 6)}`;

let status: ServingStatus = { state: 'off', phase: 'Not started' };
let starting: Promise<void> | null = null;
let lastCheck = 0;

function set(patch: Partial<ServingStatus>) {
	status = { ...status, ...patch };
	if (patch.state) status.since = new Date().toISOString();
}

/** The endpoint can change only while nothing is up on the current one. */
export async function canSwitchEndpoint(key: string) {
	await getStatus(key);
	return !starting && (status.state === 'off' || status.state === 'error') && !status.deployment;
}

/** Forget the current endpoint's status after a switch; its ids stay in its own state file. */
export function resetStatus() {
	status = { state: 'off', phase: 'Not started' };
	lastCheck = 0;
}

/** Check a key works: the `mga` blueprint must be visible to it. */
export async function checkKey(key: string): Promise<string | null> {
	const [code, body] = await new Atai(key).raw('GET', '/agents/blueprints/mga');
	if (code === 200) return null;
	if (code === 401 || code === 403) return 'The API key was rejected.';
	if (code === 404) return 'The `mga` blueprint is not visible to this API key.';
	return `Could not reach ${endpoint()} (${code || 'network error'}): ${String(JSON.stringify(body)).slice(0, 200)}`;
}

/**
 * Values must be declared by the blueprint AND referenced as ${values.<key>},
 * or they are accepted, echoed back, and ignored.
 */
async function unwiredValues(api: Atai): Promise<string[]> {
	const doc = (await api.req('GET', '/agents/blueprints/mga')).document ?? {};
	const wired = JSON.stringify({ nodes: doc.nodes, connectors: doc.connectors });
	return Object.keys(MGA_VALUES).filter(
		(k) => !(k in (doc.values ?? {})) || !wired.includes(`\${values.${k}}`)
	);
}

async function ensureProject(api: Atai, s: State): Promise<string> {
	if (s.project && (await api.alive(`/projects/${s.project}`))) return s.project;
	const found = (await api.listAll('/projects')).find((p) => p.name === PROJECT_NAME);
	s.project = found
		? found.id
		: (
				await api.req('POST', '/projects', {
					body: {
						name: PROJECT_NAME,
						description: 'Procedure videos to step-by-step manuals (MGA)'
					}
				})
			).id;
	save(s);
	return s.project!;
}

async function ensureBlueprint(api: Atai, s: State, project: string): Promise<string> {
	if (s.blueprint && (await api.alive(`/agents/blueprints/${s.blueprint}`))) return s.blueprint;
	const key = settingKey();
	const list = await api.req('GET', `/agents/blueprints?project_id=${project}&key=${key}`);
	const found = (list?.data ?? []).find((b: Json) => b.blueprint_key === key);
	s.blueprint = found
		? found.id
		: (
				await api.req('POST', '/agents/blueprints/mga/copy', {
					body: {
						project_id: project,
						blueprint_key: key,
						name: `MGA ${key}`,
						overrides: { values: MGA_VALUES }
					}
				})
			).id;
	delete s.package;
	delete s.deployment;
	save(s);
	return s.blueprint!;
}

async function ensurePackage(api: Atai, s: State, blueprint: string): Promise<string> {
	for (let attempt = 0; attempt < 2; attempt++) {
		if (!s.package || !(await api.alive(`/agents/packages/${s.package}`))) {
			const pkg = await api.req('POST', '/agents/packages', {
				body: {
					blueprint_ids: [blueprint],
					target: { kind: 'platform' },
					name: `MGA ${settingKey()}`
				}
			});
			s.package = pkg.id;
			delete s.deployment;
			save(s);
		}
		set({ phase: 'Building the package' });
		const pkg = await api.waitWhile(`/agents/packages/${s.package}`, 'building', 15 * 60_000);
		if (pkg.status === 'ready') return pkg.id;
		if (pkg.status !== 'failed' || attempt)
			throw new Error(`package ${pkg.id} is ${pkg.status}: ${pkg.error ?? ''}`);
		await api.raw('DELETE', `/agents/packages/${s.package}`);
		delete s.package;
		save(s);
	}
	throw new Error('package never became ready');
}

/** "there was no capacity to start the deployment's agent; deploy the package again later" */
const noCapacity = (dpl: Json) => /capacity/i.test(String(dpl.error ?? dpl.status_message ?? ''));

async function ensureDeployment(api: Atai, s: State, pkg: string): Promise<string> {
	const t0 = Date.now();
	const live = (d: Json) => ['ready', 'deploying'].includes(d.status);
	let lost = 0;
	for (let attempt = 1; ; attempt++) {
		let dpl = s.deployment ? await api.alive(`/agents/deployments/${s.deployment}`) : null;
		if (dpl && !live(dpl)) {
			await api.raw('DELETE', `/agents/deployments/${dpl.id}`);
			dpl = null;
		}
		if (!dpl) {
			// A create whose response was lost may still have made one: adopt it instead of making a second.
			dpl =
				(await api.listAll(`/agents/deployments?package_id=${pkg}`)).find(
					(d) => (d.package_id ?? pkg) === pkg && d.kind === 'serving' && live(d)
				) ?? null;
		}
		if (!dpl) {
			const [code, body] = await api.raw('POST', '/agents/deployments', {
				body: { package_id: pkg, kind: 'serving' }
			});
			if (code === 0 || code >= 500) {
				if (++lost > 5)
					throw new ApiError(code, `POST /agents/deployments failed (${code || 'network'})`);
				set({ phase: 'Network error creating the deployment; checking again in 15 s' });
				await sleep(15_000);
				attempt--;
				continue;
			}
			if (code >= 300)
				throw new ApiError(
					code,
					`POST /agents/deployments failed (${code}): ${JSON.stringify(body).slice(0, 300)}`
				);
			dpl = body;
		}
		if (dpl.id !== s.deployment) {
			s.deployment = dpl.id;
			delete s.session;
			save(s);
		}
		set({ phase: 'Placing the deployment on a GPU worker', deployment: s.deployment });
		dpl = await api.waitWhile(`/agents/deployments/${s.deployment}`, 'deploying', LOAD_TIMEOUT_MS);
		if (dpl.status === 'ready') return dpl.id;
		const wait = Math.min(60_000 * attempt, 5 * 60_000);
		if (!noCapacity(dpl) || Date.now() - t0 + wait > LOAD_TIMEOUT_MS)
			throw new Error(`deployment ${dpl.id} is ${dpl.status}: ${dpl.error ?? ''}`);
		set({
			phase: `No GPU free on the platform; deploying again in ${wait / 60_000} min (attempt ${attempt + 1})`
		});
		await sleep(wait);
	}
}

async function ensureSession(api: Atai, s: State, deployment: string): Promise<string> {
	let ses = s.session ? await api.alive(`/agents/sessions/${s.session}`) : null;
	if (!ses || SESSION_TERMINAL.has(ses.status)) {
		ses = await api.req('POST', '/agents/sessions', { body: { deployment_id: deployment } });
		s.session = ses.id;
		save(s);
	}
	set({ state: 'loading', phase: 'Loading the model (about 15 minutes)', session: s.session });
	ses = await api.waitWhile(`/agents/sessions/${s.session}`, 'pending', LOAD_TIMEOUT_MS, 10_000);
	if (ses.status !== 'running')
		throw new Error(`session ${ses.id} is ${ses.status}: ${ses.error ?? ''}`);
	return ses.id;
}

async function bringUp(key: string) {
	const api = new Atai(key);
	const s = load();
	set({
		state: 'starting',
		phase: 'Checking the mga blueprint',
		error: undefined,
		setting: settingKey()
	});
	const unwired = await unwiredValues(api);
	if (unwired.length) console.warn(`[serving] mga does not use: ${unwired.join(', ')}`);
	set({ phase: 'Finding the project' });
	const project = await ensureProject(api, s);
	set({ phase: 'Copying the mga blueprint' });
	const blueprint = await ensureBlueprint(api, s, project);
	const pkg = await ensurePackage(api, s, blueprint);
	const deployment = await ensureDeployment(api, s, pkg);
	await ensureSession(api, s, deployment);
	lastCheck = Date.now();
	set({ state: 'ready', phase: 'Ready for videos' });
}

/** Start serving in the background; resolves when it is ready or has failed. */
export function start(key: string): Promise<void> {
	if (!starting) {
		starting = bringUp(key)
			.catch((e) => {
				set({ state: 'error', phase: 'Failed to start', error: (e as Error).message });
				throw e;
			})
			.finally(() => {
				starting = null;
			});
		starting.catch(() => {});
	}
	return starting;
}

/** Stop and delete the session and the deployment. The copy and package stay for next time. */
export async function stop(key: string) {
	const api = new Atai(key);
	const s = load();
	set({ state: 'stopping', phase: 'Stopping' });
	// Every session on the deployment, not only the recorded one: a lost create can leave another.
	const sessions = new Set(s.session ? [s.session] : []);
	if (s.deployment)
		for (const ses of await api
			.listAll(`/agents/sessions?deployment_id=${s.deployment}`)
			.catch(() => []))
			sessions.add(ses.id);
	for (const id of sessions) {
		const ses = await api.alive(`/agents/sessions/${id}`);
		if (ses && !SESSION_TERMINAL.has(ses.status))
			await api.raw('POST', `/agents/sessions/${id}/cancel`, { body: {}, idempotent: true });
		await api.raw('DELETE', `/agents/sessions/${id}`);
	}
	if (s.deployment) {
		const dpl = await api.alive(`/agents/deployments/${s.deployment}`);
		if (dpl && !['stopped', 'failed'].includes(dpl.status))
			await api.raw('POST', `/agents/deployments/${s.deployment}/stop`, {
				body: {},
				idempotent: true
			});
		await api.raw('DELETE', `/agents/deployments/${s.deployment}`);
	}
	delete s.session;
	delete s.deployment;
	save(s);
	set({
		state: 'off',
		phase: 'Stopped',
		session: undefined,
		deployment: undefined,
		error: undefined
	});
}

/**
 * The current status. With a key, a recorded session is checked against the
 * platform (at most every 15 s), which also picks serving back up after a
 * server restart.
 */
export async function getStatus(key?: string): Promise<ServingStatus> {
	if (starting || !key || status.state === 'stopping') return status;
	const s = load();
	if (!s.session) {
		if (status.state === 'ready' || status.state === 'loading')
			set({ state: 'off', phase: 'Not started' });
		// A deployment left from before a restart still holds a GPU: show it so it can be stopped.
		if (s.deployment && !status.deployment && Date.now() - lastCheck >= 15_000) {
			lastCheck = Date.now();
			const dpl = await new Atai(key).alive(`/agents/deployments/${s.deployment}`);
			if (dpl && ['ready', 'deploying'].includes(dpl.status))
				set({
					phase:
						'A deployment from before is still up: Start to use it, or Stop to release the GPU',
					deployment: s.deployment
				});
		}
		return status;
	}
	if (Date.now() - lastCheck < 15_000 && status.state !== 'off') return status;
	lastCheck = Date.now();
	const ses = await new Atai(key).alive(`/agents/sessions/${s.session}`);
	if (ses?.status === 'running') {
		set({
			...(status.state !== 'ready' ? { state: 'ready' as const } : {}),
			phase: 'Ready for videos',
			session: s.session,
			deployment: s.deployment,
			setting: settingKey()
		});
	} else if (ses?.status === 'pending') {
		start(key); // resume waiting on the load
	} else if (
		s.deployment &&
		(await new Atai(key).alive(`/agents/deployments/${s.deployment}`))?.status === 'ready'
	) {
		set({
			state: 'off',
			phase: 'Session closed after 30 idle minutes; the deployment is still up',
			session: undefined,
			deployment: s.deployment
		});
	} else {
		set({ state: 'off', phase: 'Not started', session: undefined, deployment: undefined });
	}
	return status;
}

/** A running session id, starting serving first if needed. */
export async function ensureReady(key: string): Promise<string> {
	const st = await getStatus(key);
	if (st.state !== 'ready') await start(key);
	const s = load();
	if (!s.session) throw new Error('serving did not start');
	return s.session;
}

export function bodyFor(video: Buffer, prompt: string) {
	return { model: 'mga', query: prompt, file_ids: ['base64:' + video.toString('base64')] };
}

/** One query at a time: a serving session answers one video after another. */
let queue: Promise<unknown> = Promise.resolve();

/**
 * Ask the session for a manual of one video. Retries 5xx and timeouts (a cold
 * agent answers 504); reopens the session once if it has closed (409).
 */
export function queryManual(key: string, video: Buffer, prompt: string): Promise<MgaStep[]> {
	const job = queue.then(async () => {
		const api = new Atai(key);
		const body = bodyFor(video, prompt);
		let reopened = false;
		for (let tries = 1; ; tries++) {
			const session = await ensureReady(key);
			const [code, answer] = await api.raw('POST', `/agents/sessions/${session}/query`, {
				body,
				retries: 0,
				timeoutMs: QUERY_TIMEOUT_MS
			});
			if (code === 200) {
				lastCheck = Date.now();
				const rows = answer?.response?.data;
				if (!Array.isArray(rows))
					throw new Error(`no manual in the answer: ${JSON.stringify(answer).slice(0, 300)}`);
				return rows as MgaStep[];
			}
			if (code === 409 && !reopened) {
				reopened = true;
				const s = load();
				delete s.session;
				save(s);
				set({ state: 'off', phase: 'Session closed; reopening' });
				continue;
			}
			const retryable = code === 0 || code >= 500;
			if (!retryable || tries > QUERY_RETRIES)
				throw new ApiError(
					code,
					`query failed (${code || 'network'}): ${JSON.stringify(answer).slice(0, 300)}`
				);
			await sleep(15_000);
		}
	});
	queue = job.catch(() => {});
	return job;
}
