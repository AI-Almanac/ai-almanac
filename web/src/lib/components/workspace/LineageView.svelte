<script lang="ts">
	import type { ArtifactProvenance } from '$lib/api';
	import { lineageRows } from '$lib/workspace/lineage';
	import LineageStep from './LineageStep.svelte';
	import ProducerList from './ProducerList.svelte';

	interface Props {
		workspaceId: string;
		provenance: ArtifactProvenance | null;
		error: string | null;
		onOpenTurn?: (turnId: string) => void;
	}

	let { workspaceId, provenance, error, onOpenTurn }: Props = $props();

	const rows = $derived(provenance ? lineageRows(provenance) : []);

	const lineageText = $derived(provenance?.lineage.trim() ?? '');
</script>

<div class="lineage">
	<p class="intro">The steps that produced this file, and who started or approved each one.</p>

	{#if error}
		<p class="error">{error}</p>
	{:else if !provenance}
		<p class="muted">Loading…</p>
	{:else}
		{#if provenance.steps.length === 0}
			<p class="muted">No steps are recorded for this file.</p>
		{:else}
			<ol class="steps">
				{#each rows as row (row.steps[0].id)}
					<LineageStep {workspaceId} {row} {onOpenTurn} />
				{/each}
			</ol>
			<ProducerList {provenance} {onOpenTurn} />
		{/if}
		{#if lineageText}
			<details class="trace">
				<summary>Full trace</summary>
				<pre>{lineageText}</pre>
			</details>
		{/if}
	{/if}
</div>

<style>
	.lineage {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.intro {
		margin: 0;
		font-size: 0.82rem;
		color: var(--color-text-muted);
	}
	.steps {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
	}
	.steps > :global(li + li) {
		border-top: 1px solid var(--color-border-subtle);
	}
	.trace summary {
		font-size: 0.76rem;
		color: var(--color-text-muted);
		cursor: pointer;
	}
	pre {
		margin: 0.4rem 0 0;
		padding: 0.6rem;
		border-radius: 0.4rem;
		background: var(--color-surface-muted);
		font-family: var(--font-mono);
		font-size: 0.72rem;
		overflow-x: auto;
	}
	.muted {
		margin: 0;
		font-size: 0.84rem;
		color: var(--color-text-muted);
	}
	.error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.84rem;
	}
</style>
