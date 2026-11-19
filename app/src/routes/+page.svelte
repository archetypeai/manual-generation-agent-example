<script lang="ts">
	import { onMount } from 'svelte';
	import Menubar from '@archetypeai/ds-ui-svelte-labs/primitives/menubar';
	import { EmptyState } from '@archetypeai/ds-ui-svelte-console/primitives/empty-state';
	import { Input } from '@archetypeai/ds-ui-svelte-console/primitives/input';
	import { toast } from 'svelte-sonner';
	import ApiKeyField from '#lib/components/ui/custom/ApiKeyField.svelte';
	import AgentPanel from '#lib/components/ui/custom/AgentPanel.svelte';
	import NewManual from '#lib/components/ui/custom/NewManual.svelte';
	import RunList from '#lib/components/ui/custom/RunList.svelte';
	import ManualView from '#lib/components/ui/custom/ManualView.svelte';
	import * as api from '#lib/api/client.js';
	import { isActive } from '#lib/status.js';
	import type { Run, ServingStatus } from '#lib/types.js';

	let apiKey = $state('');
	let keyOk = $state(false);
	let envKey = $state(false);
	let endpoint = $state('');
	let endpointDraft = $state('');
	let defaultEndpoint = $state('');
	let defaultPrompt = $state('');
	let serving = $state<ServingStatus>({ state: 'off', phase: 'Checking the agent' });
	let servingBusy = $state(false);
	let runs = $state<Run[]>([]);
	let selected = $state<string | null>(null);
	let uploading = $state(false);
	let progress = $state(0);

	const run = $derived(runs.find((r) => r.id === selected) ?? null);
	// A deployment lives on one endpoint: switch only while nothing is up there.
	const endpointLocked = $derived(
		(serving.state !== 'off' && serving.state !== 'error') ||
			Boolean(serving.deployment) ||
			runs.some((r) => isActive(r.status))
	);

	async function changeEndpoint(e?: SubmitEvent) {
		e?.preventDefault();
		const next = endpointDraft.trim();
		if (!next || next.replace(/\/+$/, '') === endpoint) return;
		try {
			const c = await api.setEndpoint(apiKey, next);
			endpoint = endpointDraft = c.endpoint;
			keyOk = true;
			toast.success('Endpoint changed', { description: c.endpoint });
			serving = await api.servingStatus(apiKey);
		} catch (err) {
			endpointDraft = endpoint;
			toast.error('Endpoint not changed', { description: (err as Error).message });
		}
	}

	async function setKey(key: string) {
		keyOk = false;
		if (!key && !envKey) return;
		try {
			const r = await api.checkKey(key);
			keyOk = r.ok;
			if (r.ok) {
				toast.success(key ? 'API key verified' : 'Using the API key from .env');
				serving = await api.servingStatus(key);
			} else toast.error(r.error ?? 'API key rejected');
		} catch (e) {
			toast.error((e as Error).message);
		}
	}

	async function startAgent() {
		servingBusy = true;
		try {
			serving = await api.startServing(apiKey);
		} catch (e) {
			toast.error((e as Error).message);
		} finally {
			servingBusy = false;
		}
	}

	async function stopAgent() {
		servingBusy = true;
		try {
			serving = await api.stopServing(apiKey);
			toast.success('Agent stopped; the GPU is released');
		} catch (e) {
			toast.error((e as Error).message);
		} finally {
			servingBusy = false;
		}
	}

	async function upload(file: File, prompt: string) {
		uploading = true;
		progress = 0;
		try {
			const created = await api.uploadVideo(apiKey, file, prompt, (p) => (progress = p));
			runs = [created, ...runs];
			selected = created.id;
			toast.success(`${file.name} queued`);
		} catch (e) {
			toast.error((e as Error).message);
		} finally {
			uploading = false;
		}
	}

	async function saveEdits(edits: {
		title: string;
		steps: { uid: number; instruction: string }[];
	}) {
		try {
			const updated = await api.saveEdits(run!.id, edits);
			runs = runs.map((r) => (r.id === updated.id ? updated : r));
			toast.success('Saved');
		} catch (e) {
			toast.error((e as Error).message);
		}
	}

	async function retry() {
		if (!run) return;
		try {
			const queued = await api.retryRun(apiKey, run.id);
			runs = runs.map((r) => (r.id === queued.id ? queued : r));
		} catch (e) {
			toast.error((e as Error).message);
		}
	}

	async function remove() {
		if (!run || !confirm(`Delete "${run.title}" and its video?`)) return;
		try {
			await api.deleteRun(run.id);
			runs = runs.filter((r) => r.id !== run.id);
			selected = runs[0]?.id ?? null;
		} catch (e) {
			toast.error((e as Error).message);
		}
	}

	async function refresh() {
		const list = await api.listRuns().catch(() => null);
		if (list) {
			for (const r of list) {
				const before = runs.find((x) => x.id === r.id);
				if (before && isActive(before.status) && r.status === 'completed')
					toast.success(`${r.title}: ${r.steps?.length ?? 0} steps ready`);
				if (before && isActive(before.status) && r.status === 'failed')
					toast.error(`${r.title} failed`);
			}
			runs = list;
		}
		serving = await api.servingStatus(apiKey).catch(() => serving);
	}

	onMount(() => {
		api.getConfig().then((c) => {
			endpoint = endpointDraft = c.endpoint;
			defaultEndpoint = c.defaultEndpoint;
			defaultPrompt = c.defaultPrompt;
			envKey = c.envKey;
			if (envKey && !apiKey) setKey('');
		});
		const linked = new URL(location.href).searchParams.get('run');
		refresh().then(
			() => (selected ??= runs.find((r) => r.id === linked)?.id ?? runs[0]?.id ?? null)
		);
		const t = setInterval(refresh, 4000);
		return () => clearInterval(t);
	});
</script>

<div class="flex h-screen flex-col bg-background text-foreground">
	<Menubar>
		<div class="flex items-center gap-4">
			<span class="hidden text-sm text-muted-foreground md:inline">Manual Generation</span>
			<form class="hidden md:block" onsubmit={changeEndpoint}>
				<Input
					class="w-80 font-mono"
					autocomplete="off"
					spellcheck={false}
					aria-label="API endpoint"
					title={endpointLocked
						? 'Stop the agent to change the endpoint.'
						: 'API endpoint, then Enter. A key only works on the deployment that issued it.'}
					placeholder={defaultEndpoint}
					bind:value={endpointDraft}
					disabled={endpointLocked}
					onblur={() => changeEndpoint()}
				/>
			</form>
			<ApiKeyField bind:value={apiKey} verified={keyOk} {envKey} onsubmit={setKey} />
		</div>
	</Menubar>

	<main
		class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] content-start gap-4 overflow-y-auto p-lg lg:grid-cols-[360px_minmax(0,1fr)] lg:overflow-hidden"
	>
		<aside class="flex flex-col gap-4 *:shrink-0 lg:min-h-0 lg:overflow-y-auto">
			<AgentPanel
				status={serving}
				{endpoint}
				hasKey={keyOk}
				busy={servingBusy}
				onstart={startAgent}
				onstop={stopAgent}
			/>
			<NewManual {defaultPrompt} disabled={!keyOk} busy={uploading} {progress} onfile={upload} />
			<RunList {runs} {selected} onselect={(id) => (selected = id)} />
		</aside>

		<section class="lg:min-h-0 lg:overflow-y-auto">
			{#if run}
				<ManualView {run} onsave={saveEdits} onretry={retry} ondelete={remove} />
			{:else}
				<EmptyState>
					Paste your API key in the top bar (or set MGA_API_KEY in .env), then drop a procedure
					video. The agent turns it into an ordered, timestamped manual you can edit and download as
					a PDF.
				</EmptyState>
			{/if}
		</section>
	</main>
</div>
