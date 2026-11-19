import { error, json } from '@sveltejs/kit';
import { DEFAULT_PROMPT, endpoint, envEndpoint, setEndpoint } from '#lib/server/config.js';
import { hasEnvKey, requireKey } from '#lib/server/key.js';
import { canSwitchEndpoint, checkKey, resetStatus } from '#lib/server/serving.js';
import { listRuns } from '#lib/server/store.js';
import { isActive } from '#lib/status.js';
import type { RequestHandler } from './$types';

const config = () => ({
	endpoint: endpoint(),
	defaultEndpoint: envEndpoint(),
	defaultPrompt: DEFAULT_PROMPT,
	envKey: hasEnvKey()
});

export const GET: RequestHandler = () => json(config());

/** Switch endpoint (held in memory until a restart). Kept only if the key works there. */
export const POST: RequestHandler = async ({ request }) => {
	const key = requireKey(request);
	const raw = String((await request.json()).endpoint ?? '').trim();
	if (!/^https?:\/\/[^/\s]+/.test(raw)) error(400, `Not an http(s) URL: ${raw}`);
	if (!(await canSwitchEndpoint(key))) error(409, 'Stop the agent before changing the endpoint.');
	if ((await listRuns()).some((r) => isActive(r.status)))
		error(409, 'Wait for the current manual to finish before changing the endpoint.');
	const old = endpoint();
	setEndpoint(raw);
	const problem = await checkKey(key);
	if (problem) {
		setEndpoint(old);
		error(400, problem);
	}
	if (endpoint() !== old) resetStatus();
	return json(config());
};
