<script lang="ts">
	import './layout.css';
	import favicon from '#lib/assets/favicon.svg';
	import { onMount } from 'svelte';
	import { Toaster } from '@archetypeai/ds-ui-svelte-console/primitives/sonner';
	import { darkMode } from '@archetypeai/ds-ui-svelte-console/primitives/theme';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	onMount(() => darkMode.set(window.matchMedia('(prefers-color-scheme: dark)').matches));
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Manual Generation</title>
</svelte:head>

{@render children()}
<Toaster />
