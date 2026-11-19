import { MGA_API_ENDPOINT } from '$app/env/private';

export const DEFAULT_ENDPOINT = 'https://platform.u1.archetypeai.app/api';

/** `https://host`, `https://host/api/` and `https://host/api` all become `https://host/api`. */
export function normalizeEndpoint(raw: string): string {
	const e = raw
		.trim()
		.replace(/^["']|["']$/g, '')
		.replace(/\/+$/, '');
	return e.endsWith('/api') ? e : `${e}/api`;
}

/** Set in the app at runtime (held in memory); otherwise MGA_API_ENDPOINT, otherwise production. */
let override: string | null = null;
export const envEndpoint = () => normalizeEndpoint(MGA_API_ENDPOINT || DEFAULT_ENDPOINT);
export const endpoint = () => override ?? envEndpoint();
export const setEndpoint = (e: string) => {
	override = normalizeEndpoint(e) === envEndpoint() ? null : normalizeEndpoint(e);
};

/**
 * Say what to COVER, never how to FORMAT: the results parser owns the output
 * template, and a prompt that sets its own layout returns zero steps.
 */
export const DEFAULT_PROMPT =
	'Generate an ordered list of every distinct step performed in this video, in order. ' +
	'Include brief steps and steps that are repeated. Write each step as a direct, actionable ' +
	'instruction, as if telling the reader what to do. Do not narrate or describe what is ' +
	'happening in the video. Capture the details needed to repeat the action exactly: control ' +
	'and part names, printed and on-screen text quoted word for word, part numbers, model and ' +
	'serial numbers, tool types and sizes, quantities, and which way things move. For every ' +
	'component being handled, state where it is using grid terms such as top-left, top-right, ' +
	'bottom-left, bottom-right, center-left, or upper-center. Where several similar actions are ' +
	'performed, such as four screws on one panel, treat each as its own step and say what ' +
	'distinguishes it. Where the video shows how to tell the step worked, say that. Base every ' +
	'step on what is visible; use the spoken narration where there is any, and rely on the ' +
	'picture alone where the video is silent. Each step must describe a different action from ' +
	'the one before it — where two moments look alike, state what changed between them. Do not ' +
	'invent steps, parts, or details that are not shown or stated in the video.';

/**
 * The values set on this app's copy of `mga`. A package, a deployment and a
 * session take no values, so they live on the copy.
 *
 * max_new_tokens is sent explicitly: the blueprint default is 8192, and the model
 * reasons before answering out of the same budget, so too small a budget returns
 * an empty manual reported as success.
 */
export const MGA_VALUES = {
	max_frames: 64,
	max_new_tokens: 16384,
	// The default instruction. Each query also carries the run's own prompt.
	prompt: DEFAULT_PROMPT
};

/**
 * Prepended to every chunk after the first. Without it each chunk is treated as a
 * whole video and gets its own introduction and conclusion. The first chunk is
 * asked without it: told it came from the middle, it loses the clock.
 */
export const CHUNK_PREAMBLE =
	'This video is one segment taken from the middle of a longer recording of a single ' +
	'continuous procedure. List only the steps performed within this segment. Do not summarise, ' +
	'introduce, or conclude the overall procedure, and do not describe the segment as beginning ' +
	'or ending anything — the steps before and after it are covered elsewhere. Work at a fine ' +
	'grain: a single button press, acknowledging a dialog, reading a value, or watching an ' +
	'indicator each count as their own step, and steps only seconds apart are expected.';

export const PROJECT_NAME = 'Manual Generation App';
