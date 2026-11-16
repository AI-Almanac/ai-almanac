<script lang="ts">
	import type { WorkspaceJob } from '$lib/api/workspaces';
	import {
		addableRunDetail,
		addableRuns,
		findAddableRuns,
		type AddableRun
	} from '$lib/workspace/addable-runs';
	import type { Run } from '$lib/workspace/runs';

	interface Props {
		/** The user's runs that are not in the workspace yet. */
		load: () => Promise<WorkspaceJob[]>;
		onAdd: (run: Run) => Promise<void>;
		onClose: () => void;
	}

	let { load, onAdd, onClose }: Props = $props();

	let runs = $state<AddableRun[] | null>(null);
	let query = $state('');
	let adding = $state<string | null>(null);
	let error = $state<string | null>(null);
	const found = $derived(findAddableRuns(runs ?? [], query));

	$effect(() => {
		load()
			.then((jobs) => (runs = addableRuns(jobs)))
			.catch(() => (error = 'Your runs could not be loaded. Close this and try again.'));
	});

	async function add(item: AddableRun) {
		adding = item.run.key;
		error = null;
		try {
			await onAdd(item.run);
		} catch {
			error = `“${item.title}” could not be added. Try again.`;
		} finally {
			adding = null;
		}
	}
</script>

<section class="picker" aria-labelledby="run-picker-heading">
	<header class="head">
		<h2 id="run-picker-heading">Add an existing run</h2>
		<button type="button" class="close" onclick={onClose}>Cancel</button>
	</header>
	<p class="lead">
		Benchmarks, blends and forecasts you have already run. Adding one brings its results and files
		into this workspace; the run itself is not changed.
	</p>
	<input
		class="find"
		type="search"
		placeholder="Find a run by name"
		aria-label="Find a run by name"
		bind:value={query}
	/>
	{#if error}<p class="error" role="alert">{error}</p>{/if}

	{#if runs === null}
		{#if !error}<p class="note">Loading…</p>{/if}
	{:else if runs.length === 0}
		<p class="note">
			Every run of yours is already here. A run you start on the Benchmarks, Blends or Forecasts
			page shows up in this list.
		</p>
	{:else if found.length === 0}
		<p class="note">No run has “{query.trim()}” in its name.</p>
	{:else}
		<ul class="runs">
			{#each found as item (item.run.key)}
				<li>
					<button type="button" class="run" disabled={adding !== null} onclick={() => add(item)}>
						<span class="title">{item.title}</span>
						<span class="detail">
							{adding === item.run.key ? 'Adding…' : addableRunDetail(item)}
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.picker {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		width: min(100%, 40rem);
		min-height: 0;
		margin: 0 auto;
	}
	.head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-3);
	}
	h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-title);
	}
	.close {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
	}
	.lead,
	.note {
		margin: 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-body);
	}
	.find {
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface-raised);
		color: var(--color-text);
		font-size: var(--text-size-body);
	}
	.runs {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.run {
		display: flex;
		flex-direction: column;
		gap: 0;
		width: 100%;
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
		background: var(--color-surface-raised);
		color: var(--color-text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.run:hover:not(:disabled),
	.run:focus-visible {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}
	.run:disabled {
		cursor: progress;
	}
	.title {
		font-size: var(--text-size-body);
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.detail {
		color: var(--color-text-muted);
		font-size: var(--text-size-caption);
	}
	.error {
		margin: 0;
		color: var(--color-danger);
		font-size: var(--text-size-label);
	}
</style>
