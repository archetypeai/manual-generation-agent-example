/**
 * A minimal client for the Archetype AI Agents API (Agent Lifecycle 2.0).
 *
 * Only calls that are safe to repeat retry on a network error or 502/503/504:
 * GET, DELETE, and POSTs marked idempotent (cancel, stop). A create POST never
 * retries — a repeat could make a second deployment that holds a GPU.
 */
import { endpoint } from './config';

/** Platform JSON is untyped at this boundary; callers read the fields they need. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export type Call = {
	body?: unknown;
	idempotent?: boolean;
	timeoutMs?: number;
	retries?: number;
};

export class Atai {
	readonly base: string;
	constructor(readonly apiKey: string) {
		this.base = endpoint();
	}

	/** Returns [status, json]. Status 0 is a network error or timeout. */
	async raw(method: string, path: string, call: Call = {}): Promise<[number, Json]> {
		const safe = call.idempotent ?? (method === 'GET' || method === 'DELETE');
		const retries = safe ? (call.retries ?? 4) : 0;
		const url = path.startsWith('http') ? path : `${this.base}${path}`;
		for (let attempt = 0; ; attempt++) {
			let status = 0;
			let payload: Json;
			try {
				const res = await fetch(url, {
					method,
					headers: {
						Authorization: `Bearer ${this.apiKey}`,
						...(call.body !== undefined ? { 'Content-Type': 'application/json' } : {})
					},
					body: call.body !== undefined ? JSON.stringify(call.body) : undefined,
					signal: AbortSignal.timeout(call.timeoutMs ?? 120_000)
				});
				status = res.status;
				const text = await res.text();
				try {
					payload = text ? JSON.parse(text) : null;
				} catch {
					payload = text;
				}
			} catch (e) {
				payload = (e as Error).message;
			}
			const transient = status === 0 || status === 502 || status === 503 || status === 504;
			if (transient && attempt < retries) {
				await sleep(5000 * 2 ** attempt);
				continue;
			}
			return [status, payload];
		}
	}

	async req(method: string, path: string, call: Call = {}): Promise<Json> {
		const [status, payload] = await this.raw(method, path, call);
		if (status >= 200 && status < 300) return payload;
		const detail = typeof payload === 'string' ? payload : JSON.stringify(payload);
		throw new ApiError(
			status,
			`${method} ${path} failed (${status || 'network'}): ${detail?.slice(0, 400)}`
		);
	}

	/** The object, or null if it is gone. */
	async alive(path: string): Promise<Json | null> {
		const [status, obj] = await this.raw('GET', path);
		return status === 200 && obj && typeof obj === 'object' ? obj : null;
	}

	async listAll(path: string): Promise<Json[]> {
		const items: Json[] = [];
		let after = '';
		const sep = path.includes('?') ? '&' : '?';
		for (;;) {
			const page = await this.req('GET', `${path}${sep}limit=1000${after}`);
			items.push(...(page?.data ?? []));
			if (!page?.has_more) return items;
			after = `&after=${encodeURIComponent(page.next_cursor)}`;
		}
	}

	/** Poll while the object's status is `waiting`; return it once it is not. */
	async waitWhile(path: string, waiting: string, timeoutMs: number, everyMs = 5000): Promise<Json> {
		const t0 = Date.now();
		let obj = await this.req('GET', path);
		while (obj.status === waiting) {
			if (Date.now() - t0 > timeoutMs)
				throw new Error(`${obj.id} still ${waiting} after ${Math.round(timeoutMs / 1000)} s`);
			await sleep(everyMs);
			obj = await this.req('GET', path);
		}
		return obj;
	}
}
