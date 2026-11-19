<script lang="ts">
	import * as InputGroup from '@archetypeai/ds-ui-svelte-console/primitives/input-group';
	import { Eye, EyeOff, Check } from '@lucide/svelte';

	let {
		value = $bindable(''),
		verified = false,
		envKey = false,
		onsubmit
	}: {
		value?: string;
		verified?: boolean;
		envKey?: boolean;
		onsubmit: (key: string) => void;
	} = $props();

	let shown = $state(false);
	let draft = $state('');

	function submit(e: SubmitEvent) {
		e.preventDefault();
		value = draft.trim();
		onsubmit(value);
	}
</script>

<form class="w-72" onsubmit={submit}>
	<InputGroup.Root>
		<InputGroup.Input
			type={shown ? 'text' : 'password'}
			placeholder={envKey ? 'Using .env key; paste to override' : 'Paste your API key, then Enter'}
			aria-label="Archetype AI API key"
			autocomplete="off"
			spellcheck={false}
			bind:value={draft}
			onblur={() => draft.trim() !== value && ((value = draft.trim()), onsubmit(value))}
		/>
		<InputGroup.Addon align="inline-end">
			{#if verified}
				<Check class="size-4 stroke-icon-status text-atai-good" aria-label="API key verified" />
			{/if}
			<InputGroup.Button
				size="icon-sm"
				type="button"
				aria-label={shown ? 'Hide API key' : 'Show API key'}
				onclick={() => (shown = !shown)}
			>
				{#if shown}<EyeOff aria-hidden="true" />{:else}<Eye aria-hidden="true" />{/if}
			</InputGroup.Button>
		</InputGroup.Addon>
	</InputGroup.Root>
</form>
