<script lang="ts">
	import * as Card from '@archetypeai/ds-ui-svelte-console/primitives/card';
	import * as Alert from '@archetypeai/ds-ui-svelte-console/primitives/alert';
	import { Badge } from '@archetypeai/ds-ui-svelte-console/primitives/badge';
	import { Button } from '@archetypeai/ds-ui-svelte-console/primitives/button';
	import { Input } from '@archetypeai/ds-ui-svelte-console/primitives/input';
	import { Label } from '@archetypeai/ds-ui-svelte-console/primitives/label';
	import { Progress } from '@archetypeai/ds-ui-svelte-console/primitives/progress';
	import { Textarea } from '@archetypeai/ds-ui-svelte-console/primitives/textarea';
	import VideoPlayer from '@archetypeai/ds-ui-svelte-labs/primitives/video-player';
	import { Download, RotateCcw, Trash2, Undo2 } from '@lucide/svelte';
	import { cn } from '#lib/utils.js';
	import { runBadge, isActive } from '#lib/status.js';
	import { fmt, type Note, type Run } from '#lib/types.js';

	let {
		run,
		onsave,
		onretry,
		ondelete
	}: {
		run: Run;
		onsave: (edits: {
			title: string;
			steps: { uid: number; instruction: string }[];
		}) => Promise<void>;
		onretry: () => void;
		ondelete: () => void;
	} = $props();

	type Draft = { uid: number; instruction: string; removed: boolean };
	let title = $state('');
	let drafts = $state<Draft[]>([]);
	let loadedFor = $state('');
	let saving = $state(false);

	// Reset the editor when another run is selected or this one finishes.
	$effect(() => {
		const sig = `${run.id}:${run.status}:${run.steps?.length ?? 0}`;
		if (sig !== loadedFor) {
			loadedFor = sig;
			title = run.title;
			drafts = (run.steps ?? []).map((s) => ({
				uid: s.uid,
				instruction: s.instruction,
				removed: false
			}));
			time = 0;
			paused = true;
		}
	});

	const byUid = $derived(new Map((run.steps ?? []).map((s) => [s.uid, s])));
	const dirty = $derived(
		title.trim() !== run.title ||
			drafts.some((d) => d.removed || d.instruction.trim() !== byUid.get(d.uid)?.instruction)
	);
	const kept = $derived(drafts.filter((d) => !d.removed));

	let time = $state(0);
	let paused = $state(true);
	let duration = $state(0);
	$effect(() => {
		if (!duration && run.probe) duration = run.probe.durationSec;
	});
	const markers = $derived(
		kept.map((d, i) => {
			const s = byUid.get(d.uid)!;
			return { start: s.timestamp_start, end: s.timestamp_end, label: `Step ${i + 1}` };
		})
	);
	const current = $derived(
		kept.findIndex((d) => {
			const s = byUid.get(d.uid)!;
			return time >= s.timestamp_start && time < s.timestamp_end;
		})
	);

	async function save() {
		saving = true;
		try {
			await onsave({
				title: title.trim() || run.title,
				steps: kept.map((d) => ({ uid: d.uid, instruction: d.instruction }))
			});
		} finally {
			saving = false;
		}
	}

	const notes = $derived(
		run.warnings.map((w) =>
			typeof w === 'string'
				? {
						kind: /check|dropped|failed|no audio/i.test(w) ? ('check' as const) : ('info' as const),
						text: w
					}
				: w
		)
	);
	const checks = $derived(notes.filter((n) => n.kind === 'check'));
	const infos = $derived(notes.filter((n) => n.kind === 'info'));
	const seekable = $derived(run.status === 'completed' && Boolean(run.steps));

	const chunksDone = $derived(run.chunks?.filter((c) => c.steps !== null).length ?? 0);
</script>

<div class="flex h-full min-h-0 flex-col gap-4">
	<header class="flex flex-wrap items-center gap-3">
		{#if run.status === 'completed'}
			<Input class="max-w-md text-lg" bind:value={title} aria-label="Manual title" />
		{:else}
			<h2 class="normal-case">{run.title}</h2>
		{/if}
		<Badge variant="status" class={runBadge[run.status].class}>{runBadge[run.status].label}</Badge>
		<div class="ml-auto flex items-center gap-2">
			{#if run.status === 'completed'}
				{#if dirty}
					<Button variant="outline" size="sm" disabled={saving} onclick={() => (loadedFor = '')}>
						<Undo2 aria-hidden="true" /> Discard
					</Button>
					<Button size="sm" loading={saving} onclick={save}>Save changes</Button>
				{:else}
					<Button size="sm" href={`/api/runs/${run.id}/pdf`} download>
						<Download aria-hidden="true" /> Download PDF
					</Button>
				{/if}
			{/if}
			{#if run.status === 'failed'}
				<Button size="sm" onclick={onretry}>
					<RotateCcw aria-hidden="true" /> Retry
				</Button>
			{/if}
			{#if !isActive(run.status)}
				<Button
					variant="destructiveIcon"
					size="icon"
					aria-label="Delete this manual"
					onclick={ondelete}
				>
					<Trash2 aria-hidden="true" />
				</Button>
			{/if}
		</div>
	</header>

	<p class="text-xs text-muted-foreground">
		{run.videoName}
		{#if run.probe}
			· <span class="font-mono">{fmt(run.probe.durationSec)}</span> ·
			<span class="font-mono">{run.probe.width}×{run.probe.height}</span>
			· {run.probe.hasAudio ? 'audio' : 'no audio'}
		{/if}
	</p>

	{#snippet noteList(list: Note[])}
		<ul class="list-disc pl-4">
			{#each list as n, i (i)}
				<li>
					{#if n.start !== undefined && n.end !== undefined}
						{#if seekable}
							<Button
								variant="link"
								size="inline"
								class="font-mono"
								aria-label={`Play from ${fmt(n.start)}`}
								onclick={() => ((time = n.start!), (paused = false))}
							>
								{fmt(n.start)}–{fmt(n.end)}
							</Button>
						{:else}
							<span class="font-mono">{fmt(n.start)}–{fmt(n.end)}</span>
						{/if}
						·
					{/if}
					{n.text}
				</li>
			{/each}
		</ul>
	{/snippet}

	{#if checks.length}
		<Alert.Root variant="warning">
			<Alert.Title>Check before downloading</Alert.Title>
			<Alert.Description>{@render noteList(checks)}</Alert.Description>
		</Alert.Root>
	{/if}
	{#if infos.length}
		<Alert.Root variant="neutral">
			<Alert.Title>How this video was processed</Alert.Title>
			<Alert.Description>{@render noteList(infos)}</Alert.Description>
		</Alert.Root>
	{/if}

	{#if run.status === 'failed'}
		<Alert.Root variant="danger">
			<Alert.Title>Generation failed</Alert.Title>
			<Alert.Description>{run.error}</Alert.Description>
		</Alert.Root>
	{:else if isActive(run.status)}
		<Card.Root>
			<Card.Content class="flex flex-col gap-3">
				<p class="text-sm">{run.phase}</p>
				<Progress
					value={run.chunks?.length ? (chunksDone / run.chunks.length) * 100 : null}
					variant="running"
					aria-label="Generation progress"
				/>
				{#if run.status === 'waiting'}
					<p class="text-xs text-muted-foreground">
						The agent is loading its models on a GPU. This happens once; later videos take seconds
						while the agent stays up.
					</p>
				{/if}
				{#if run.chunks?.length}
					<p class="text-xs text-muted-foreground">
						<span class="font-mono">{chunksDone}/{run.chunks.length}</span> chunks of 90 s with 15 s overlap
					</p>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}

	{#if run.status === 'completed' && run.steps}
		<div class="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
			<div class="flex flex-col gap-2">
				<VideoPlayer
					src={`/api/runs/${run.id}/video`}
					bind:time
					bind:paused
					bind:duration
					{markers}
					controlsVisibility="always"
				/>
				<p class="text-xs text-muted-foreground">
					Click a step to jump to it. Edit text inline; remove steps you don't need.
				</p>
			</div>

			<ol class="flex min-h-0 flex-col gap-2 overflow-y-auto pr-1">
				{#each drafts as d (d.uid)}
					{@const s = byUid.get(d.uid)!}
					{@const n = kept.indexOf(d)}
					<li>
						<Card.Root
							class={cn(
								'gap-0 py-0',
								d.removed && 'opacity-50',
								n === current && !paused && 'border-foreground'
							)}
						>
							<div class="flex gap-3 p-3">
								<button
									type="button"
									class="w-32 shrink-0 overflow-hidden rounded-interactive"
									aria-label={`Play step ${n + 1}`}
									onclick={() => ((time = s.timestamp_start), (paused = false))}
								>
									{#if s.frame}
										<img
											src={`/api/runs/${run.id}/frames/${s.frame}`}
											alt=""
											class="aspect-video w-full object-cover"
											loading="lazy"
										/>
									{:else}
										<div class="aspect-video w-full bg-muted"></div>
									{/if}
								</button>
								<div class="flex min-w-0 flex-1 flex-col gap-1">
									<div class="flex items-center gap-2">
										{#if !d.removed}<Badge variant="step" size="sm" aria-hidden="true">
												{n + 1}
											</Badge>{/if}
										<span class="font-mono text-xs text-muted-foreground">
											{fmt(s.timestamp_start)}–{fmt(s.timestamp_end)}
										</span>
										<Button
											variant={d.removed ? 'ghost' : 'destructiveIcon'}
											size="icon"
											class="ml-auto"
											aria-label={d.removed ? `Restore step` : `Remove step ${n + 1}`}
											onclick={() => (d.removed = !d.removed)}
										>
											{#if d.removed}<Undo2 aria-hidden="true" />{:else}<Trash2
													aria-hidden="true"
												/>{/if}
										</Button>
									</div>
									<Textarea
										size="sm"
										bind:value={d.instruction}
										disabled={d.removed}
										aria-label={`Instruction for step ${n + 1}`}
										class="min-h-0 resize-y"
									/>
								</div>
							</div>
						</Card.Root>
					</li>
				{/each}
			</ol>
		</div>
		<Label variant="sectionHeader" class="text-muted-foreground">
			<span class="font-mono">{kept.length}</span> steps · generated {new Date(
				run.finishedAt ?? run.createdAt
			).toLocaleString()}
		</Label>
	{/if}
</div>
