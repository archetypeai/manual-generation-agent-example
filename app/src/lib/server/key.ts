import { error } from '@sveltejs/kit';
import { MGA_API_KEY } from '$app/env/private';

/** A key pasted in the browser (sent per request) wins; otherwise MGA_API_KEY from .env. Never sent to the browser. */
export const hasEnvKey = () => Boolean(MGA_API_KEY);

export function keyOf(request: Request): string | null {
	return request.headers.get('x-atai-key')?.trim() || MGA_API_KEY || null;
}

export function requireKey(request: Request): string {
	const key = keyOf(request);
	if (!key) error(401, 'Set your Archetype AI API key first.');
	return key;
}
