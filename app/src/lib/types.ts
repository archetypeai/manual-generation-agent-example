/** One step as MGA returns it. `step` is 0-based; timestamps are seconds in the source video. */
export type MgaStep = {
	step: number;
	instruction: string;
	frame_start: number;
	frame_end: number;
	timestamp_start: number;
	timestamp_end: number;
};

/** A step in a manual: MGA's output plus a stable id and the frame shown for it. */
export type Step = MgaStep & { uid: number; frame?: string };

export type Probe = {
	durationSec: number;
	width: number;
	height: number;
	fps: number;
	hasAudio: boolean;
	sizeBytes: number;
};

/** `check`: something to verify before downloading; `info`: how the video was processed. */
export type Note = { kind: 'check' | 'info'; text: string; start?: number; end?: number };

export type RunStatus =
	'queued' | 'preparing' | 'waiting' | 'generating' | 'finishing' | 'completed' | 'failed';

export type Run = {
	id: string;
	title: string;
	videoName: string;
	videoFile: string;
	probe?: Probe;
	prompt: string;
	createdAt: string;
	status: RunStatus;
	phase: string;
	/** Plain strings are from runs made before notes had a kind. */
	warnings: (Note | string)[];
	error?: string;
	chunks?: { index: number; offset: number; duration: number; steps: number | null }[];
	steps?: Step[];
	finishedAt?: string;
};

export type ServingState = 'off' | 'starting' | 'loading' | 'ready' | 'stopping' | 'error';

export type ServingStatus = {
	state: ServingState;
	phase: string;
	since?: string;
	error?: string;
	deployment?: string;
	session?: string;
	setting?: string;
};

export const fmt = (s: number) =>
	`${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
