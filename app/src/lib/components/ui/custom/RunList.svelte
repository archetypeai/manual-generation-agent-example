<script lang="ts">
	import * as Card from '@archetypeai/ds-ui-svelte-console/primitives/card';
	import * as Item from '@archetypeai/ds-ui-svelte-console/primitives/item';
	import { Badge } from '@archetypeai/ds-ui-svelte-console/primitives/badge';
	import { EmptyState } from '@archetypeai/ds-ui-svelte-console/primitives/empty-state';
	import { cn } from '#lib/utils.js';
	import { runBadge } from '#lib/status.js';
	import { fmt, type Run } from '#lib/types.js';

	const omitChildren = (p: Record<string, unknown>) =>
		Object.fromEntries(Object.entries(p).filter(([k]) => k !== 'children'));

	let {
		runs,
		selected,
		onselect
	}: { runs: Run[]; selected: string | null; onselect: (id: string) => void } = $props();
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>Manuals</Card.Title>
	</Card.Header>
	<Card.Content>
		{#if runs.length === 0}
			<EmptyState variant="list">No manuals yet. Upload a video to make one.</EmptyState>
		{:else}
			<Item.Group class="gap-2">
				{#each runs as run (run.id)}
					<Item.Root class={cn(run.id === selected && 'border-foreground')}>
						{#snippet child({ props })}
							<button
								{...omitChildren(props)}
								type="button"
								class={cn(props.class as string, 'w-full text-left')}
								onclick={() => onselect(run.id)}
							>
								<Item.Content class="min-w-0">
									<Item.Title class="truncate">{run.title}</Item.Title>
									<Item.Description>
										<span class="font-mono">{run.probe ? fmt(run.probe.durationSec) : '–'}</span>
										{#if run.steps}· <span class="font-mono">{run.steps.length}</span> steps{/if}
										· {new Date(run.createdAt).toLocaleDateString()}
									</Item.Description>
								</Item.Content>
								<Item.Actions>
									<Badge variant="status" size="sm" class={runBadge[run.status].class}>
										{runBadge[run.status].label}
									</Badge>
								</Item.Actions>
							</button>
						{/snippet}
					</Item.Root>
				{/each}
			</Item.Group>
		{/if}
	</Card.Content>
</Card.Root>
