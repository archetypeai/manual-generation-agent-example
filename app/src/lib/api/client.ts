/** Browser-side calls. A pasted key lives in page state only and rides along as a header; without one the server uses MGA_API_KEY. */
import type { Run, ServingStatus } from '#lib/types.js';

async function call<T>(key: string, method: string, url: string, body?: unknown): Promise<T> {
	const res = await fetch(url, {
		method,
		headers: {
			...(key ? { 'x-atai-key': key } : {}),
			// A mutating request with no content type is treated as a cross-site form post.
			...(method !== 'GET' ? { 'Content-Type': 'application/json' } : {})
		},
		body: body !== undefined ? JSON.stringify(body) : undefined
	});
	if (!res.ok) {
		const msg = await res
			.json()
			.then((j) => j.message ?? j.error)
			.catch(() => res.statusText);
		throw new Error(msg || `HTTP ${res.status}`);
	}
	return res.status === 204 ? (undefined as T) : res.json();
}

export type Config = {
	endpoint: string;
	defaultEndpoint: string;
	defaultPrompt: string;
	envKey: boolean;
};
export const getConfig = () => call<Config>('', 'GET', '/api/config');
export const setEndpoint = (key: string, endpoint: string) =>
	call<Config>(key, 'POST', '/api/config', { endpoint });
export const checkKey = (key: string) =>
	call<{ ok: boolean; error?: string }>(key, 'POST', '/api/serving', { action: 'check' });
export const servingStatus = (key: string) => call<ServingStatus>(key, 'GET', '/api/serving');
export const startServing = (key: string) =>
	call<ServingStatus>(key, 'POST', '/api/serving', { action: 'start' });
export const stopServing = (key: string) =>
	call<ServingStatus>(key, 'POST', '/api/serving', { action: 'stop' });
export const listRuns = () => call<Run[]>('', 'GET', '/api/runs');
export const getRun = (id: string) => call<Run>('', 'GET', `/api/runs/${id}`);
export const saveEdits = (
	id: string,
	edits: { title?: string; steps?: { uid: number; instruction: string }[] }
) => call<Run>('', 'PATCH', `/api/runs/${id}`, edits);
export const retryRun = (key: string, id: string) => call<Run>(key, 'POST', `/api/runs/${id}`, {});
export const deleteRun = (id: string) => call<void>('', 'DELETE', `/api/runs/${id}`);

/** Upload with progress (fetch has no upload progress). */
export function uploadVideo(
	key: string,
	file: File,
	prompt: string,
	onProgress: (pct: number) => void
): Promise<Run> {
	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		xhr.open('POST', `/api/runs?name=${encodeURIComponent(file.name)}`);
		if (key) xhr.setRequestHeader('x-atai-key', key);
		xhr.setRequestHeader('x-mga-prompt', encodeURIComponent(prompt));
		xhr.setRequestHeader('Content-Type', 'application/octet-stream');
		xhr.upload.onprogress = (e) =>
			e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
		xhr.onload = () => {
			let body: { message?: string } | null = null;
			try {
				body = JSON.parse(xhr.responseText);
			} catch {
				/* not json */
			}
			if (xhr.status >= 200 && xhr.status < 300) resolve(body as unknown as Run);
			else reject(new Error(body?.message ?? `Upload failed (${xhr.status})`));
		};
		xhr.onerror = () => reject(new Error('Upload failed: network error'));
		xhr.send(file);
	});
}
