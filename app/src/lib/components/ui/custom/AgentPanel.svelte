<script lang="ts">
	import * as Card from '@archetypeai/ds-ui-svelte-console/primitives/card';
	import { Badge } from '@archetypeai/ds-ui-svelte-console/primitives/badge';
	import { Button } from '@archetypeai/ds-ui-svelte-console/primitives/button';
	import { Label } from '@archetypeai/ds-ui-svelte-console/primitives/label';
	import { Progress } from '@archetypeai/ds-ui-svelte-console/primitives/progress';
	import { servingBadge } from '#lib/status.js';
	import type { ServingStatus } from '#lib/types.js';

	let {
		status,
		endpoint,
		hasKey,
		busy,
		onstart,
		onstop
	}: {
		status: ServingStatus;
		endpoint: string;
		hasKey: boolean;
		busy: boolean;
		onstart: () => void;
		onstop: () => void;
	} = $props();

	const badge = $derived(servingBadge[status.state]);
	const working = $derived(
		status.state === 'starting' || status.state === 'loading' || status.state === 'stopping'
	);
	const up = $derived(status.state === 'ready' || working || Boolean(status.deployment));
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>Agent</Card.Title>
		<Card.Action>
			<Badge variant="status" size="sm" class={badge.class}>{badge.label}</Badge>
		</Card.Action>
		<Card.Description>
			Manual Generation (mga) in serving: the model loads once, then each video takes seconds.
		</Card.Description>
	</Card.Header>
	<Card.Content class="flex flex-col gap-3">
		<p class="text-sm text-muted-foreground">{status.phase}</p>
		{#if working}
			<Progress value={null} variant="running" size="sm" aria-label="Agent starting" />
		{/if}
		{#if status.error}
			<p class="text-sm text-atai-critical">{status.error}</p>
		{/if}
		<div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
			<Label variant="sectionHeader">Endpoint</Label>
			<span class="truncate font-mono text-muted-foreground">{endpoint}</span>
			{#if status.deployment}
				<Label variant="sectionHeader">Deployment</Label>
				<span class="truncate font-mono text-muted-foreground">{status.deployment}</span>
			{/if}
			{#if status.session}
				<Label variant="sectionHeader">Session</Label>
				<span class="truncate font-mono text-muted-foreground">{status.session}</span>
			{/if}
		</div>
	</Card.Content>
	<Card.Footer class="flex gap-2">
		{#if !up || status.state === 'error'}
			<Button size="sm" disabled={!hasKey || busy} loading={busy} onclick={onstart}>
				Start agent
			</Button>
		{/if}
		{#if up}
			<Button
				size="sm"
				variant="outline"
				disabled={!hasKey || busy || status.state === 'stopping'}
				onclick={onstop}
			>
				Stop agent
			</Button>
		{/if}
		{#if !hasKey}
			<span class="self-center text-xs text-muted-foreground">Set an API key first.</span>
		{:else if up}
			<span class="self-center text-xs text-muted-foreground">Holds a GPU while up.</span>
		{/if}
	</Card.Footer>
</Card.Root>
