<script lang="ts">
	import * as Card from '@archetypeai/ds-ui-svelte-console/primitives/card';
	import * as DropZone from '@archetypeai/ds-ui-svelte-console/primitives/dropzone';
	import * as Collapsible from '@archetypeai/ds-ui-svelte-console/primitives/collapsible';
	import { Label } from '@archetypeai/ds-ui-svelte-console/primitives/label';
	import { Textarea } from '@archetypeai/ds-ui-svelte-console/primitives/textarea';
	import { Button } from '@archetypeai/ds-ui-svelte-console/primitives/button';
	import { Spinner } from '@archetypeai/ds-ui-svelte-console/primitives/spinner';
	import { Upload } from '@lucide/svelte';

	let {
		defaultPrompt,
		disabled,
		busy,
		progress,
		onfile
	}: {
		defaultPrompt: string;
		disabled: boolean;
		busy: boolean;
		progress: number;
		onfile: (file: File, prompt: string) => void;
	} = $props();

	let open = $state(false);
	let prompt = $state('');
	let edited = $state(false);
	$effect(() => {
		if (!edited && defaultPrompt) prompt = defaultPrompt;
	});
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>New manual</Card.Title>
		<Card.Description>
			Upload a recording of a procedure. Narrated videos under 5 minutes work best; longer ones are
			split into chunks.
		</Card.Description>
	</Card.Header>
	<Card.Content class="flex flex-col gap-3">
		<DropZone.Root
			size="compact"
			accept="video/*,.mp4,.mov,.m4v,.webm,.mkv"
			ariaLabel="Upload a procedure video"
			isUploading={busy}
			{disabled}
			onfilesSelected={(files) => files[0] && onfile(files[0], prompt)}
		>
			{#snippet idle()}
				<div class="flex flex-col items-center gap-2 p-4 text-center">
					<Upload class="size-5 stroke-icon-default" aria-hidden="true" />
					<DropZone.CaptionStack>
						<DropZone.Title>Drop a video here</DropZone.Title>
						<DropZone.Action>or browse</DropZone.Action>
						<DropZone.Hint>
							{disabled ? 'Set an API key first' : 'MP4, MOV, WebM, MKV'}
						</DropZone.Hint>
					</DropZone.CaptionStack>
				</div>
			{/snippet}
			{#snippet dragging()}
				<DropZone.Title>Release to upload</DropZone.Title>
			{/snippet}
			{#snippet uploading()}
				<div class="flex flex-col items-center gap-2 p-4">
					<Spinner size="lg" />
					<DropZone.Title>Uploading <span class="font-mono">{progress}%</span></DropZone.Title>
				</div>
			{/snippet}
		</DropZone.Root>

		<Collapsible.Root bind:open>
			<Collapsible.Trigger chevron="start">
				<Label variant="sectionHeader">Instruction</Label>
			</Collapsible.Trigger>
			<Collapsible.Content class="flex flex-col gap-2 pt-2">
				<Textarea
					size="lg"
					bind:value={prompt}
					oninput={() => (edited = true)}
					aria-label="Instruction sent with the video"
				/>
				<p class="text-xs text-muted-foreground">
					Say what to cover — never how to format. The agent owns the output layout, and a prompt
					that asks for headings or bullets returns no steps.
				</p>
				{#if prompt !== defaultPrompt}
					<Button
						variant="link"
						size="inline"
						class="self-start"
						onclick={() => ((prompt = defaultPrompt), (edited = false))}
					>
						Reset to default
					</Button>
				{/if}
			</Collapsible.Content>
		</Collapsible.Root>
	</Card.Content>
</Card.Root>
