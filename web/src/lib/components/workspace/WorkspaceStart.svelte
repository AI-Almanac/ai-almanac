<script lang="ts">
	import { startingPoints, type NewRunEntry } from '$lib/workspace/new-run-menu';
	import type { PanelName } from '$lib/workspace/panels';

	interface Props {
		/** What can be started or added here; the ones with a starting label are offered. */
		entries: NewRunEntry[];
		onNew: (panel: PanelName) => void;
		assistantAvailable: boolean;
		onOpenAssistant?: () => void;
	}

	let { entries, onNew, assistantAvailable, onOpenAssistant }: Props = $props();

	const points = $derived(startingPoints(entries));
</script>

<section class="start" aria-labelledby="workspace-start-heading">
	<h2 id="workspace-start-heading">Starting points</h2>
	<p class="lead">
		This workspace has no products yet. Benchmarks, blends, forecasts and analyses show up here as
		they are made.
	</p>
	<ul class="points">
		{#each points as entry (entry.key)}
			<li>
				<button type="button" class="point" onclick={() => onNew(entry.panel)}>
					<span>{entry.startLabel}</span>
				</button>
			</li>
		{/each}
	</ul>
	{#if assistantAvailable && onOpenAssistant}
		<p class="hint">
			Or <button type="button" class="link" onclick={onOpenAssistant}>ask the assistant</button> to set
			one up or answer a question.
		</p>
	{/if}
</section>

<style>
	.start {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		width: min(100%, 36rem);
		margin: 0 auto;
		padding: var(--space-5) var(--space-4);
	}
	h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-title);
	}
	.lead,
	.hint {
		margin: 0;
		font-size: var(--text-size-body);
		color: var(--color-text-muted);
	}
	.points {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.point {
		display: flex;
		flex-direction: column;
		gap: 0;
		width: 100%;
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface-raised);
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-body);
		font-weight: 600;
		text-align: left;
		text-decoration: none;
		cursor: pointer;
	}
	.point:hover,
	.point:focus-visible {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
</style>
