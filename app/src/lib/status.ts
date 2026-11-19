import type { RunStatus, ServingState } from '#lib/types.js';

export const runBadge: Record<RunStatus, { label: string; class: string }> = {
	queued: { label: 'Queued', class: 'text-muted-foreground' },
	preparing: { label: 'Preparing', class: 'text-atai-neutral' },
	waiting: { label: 'Starting agent', class: 'text-atai-neutral' },
	generating: { label: 'Generating', class: 'text-atai-neutral' },
	finishing: { label: 'Finishing', class: 'text-atai-neutral' },
	completed: { label: 'Completed', class: 'text-atai-good' },
	failed: { label: 'Failed', class: 'text-atai-critical' }
};

export const servingBadge: Record<ServingState, { label: string; class: string }> = {
	off: { label: 'Off', class: 'text-muted-foreground' },
	starting: { label: 'Starting', class: 'text-atai-neutral' },
	loading: { label: 'Loading', class: 'text-atai-neutral' },
	ready: { label: 'Ready', class: 'text-atai-good' },
	stopping: { label: 'Stopping', class: 'text-atai-warning' },
	error: { label: 'Failed', class: 'text-atai-critical' }
};

export const isActive = (s: RunStatus) => s !== 'completed' && s !== 'failed';
