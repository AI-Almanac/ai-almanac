<script lang="ts">
	import type { JobMetrics, MetricDefinition } from '$lib/api';
	import { compareModels, comparisonWindows, type ModelNamed } from '$lib/metric-comparison';
	import { metricMap, windowLabel } from '$lib/metric-metadata';

	interface Props {
		jobs: ModelNamed[];
		metrics: JobMetrics[];
		definitions: MetricDefinition[];
		/** The window to open on when the run has it. */
		initialWindow?: string;
		/** Mark climatology when it is the best on a metric, as the by-zone table does. */
		baselineCompetes?: boolean;
	}

	let { jobs, metrics, definitions, initialWindow, baselineCompetes = false }: Props = $props();

	const windows = $derived(comparisonWindows(metrics));
	let chosen = $state<string | null>(null);
	const window = $derived(
		chosen && windows.includes(chosen)
			? chosen
			: (windows.find((w) => w === initialWindow) ?? windows[0] ?? null)
	);
	const comparison = $derived(
		window
			? compareModels(jobs, metrics, window, metricMap(definitions), { baselineCompetes })
			: null
	);
</script>

{#if comparison && comparison.rows.length > 0}
	<figure class="comparison">
		<div class="head">
			<p class="title">Models side by side</p>
			{#if windows.length > 1}
				<div class="windows" role="group" aria-label="Lead-time window">
					{#each windows as w (w)}
						<button type="button" class:active={w === window} onclick={() => (chosen = w)}>
							{windowLabel(w)}
						</button>
					{/each}
				</div>
			{/if}
		</div>
		<div class="scroll">
			<table>
				<thead>
					<tr>
						<th scope="col">Model</th>
						{#each comparison.columns as column (column.metric)}
							<th scope="col" class="num" title={column.label}>{column.label}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each comparison.rows as row (row.model)}
						<tr class:climatology={row.climatology}>
							<th scope="row">{row.model}</th>
							{#each row.cells as cell, i (i)}
								<td class="num" class:best={cell.best}>{cell.text}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<figcaption>
			Region-wide means for {windowLabel(comparison.window).toLowerCase()}.
			{#if baselineCompetes}
				Bold marks the best value on each metric, climatology included; the last row is the
				climatology baseline.
			{:else}
				Bold marks the best model on each metric; the last row is the climatology baseline.
			{/if}
		</figcaption>
	</figure>
{/if}

<style>
	.comparison {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		margin: 0;
	}
	.head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.4rem 1rem;
	}
	.title {
		margin: 0;
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--color-text-muted);
	}
	.windows {
		display: inline-flex;
		gap: 0.2rem;
	}
	.windows button {
		padding: 0.15rem 0.55rem;
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.76rem;
		cursor: pointer;
	}
	.windows button.active {
		border-color: var(--color-accent);
		color: var(--color-accent);
		font-weight: 600;
	}
	.scroll {
		overflow: auto;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8rem;
	}
	th,
	td {
		padding: 0.3rem 0.6rem;
		border-bottom: 1px solid var(--color-border-subtle);
		text-align: left;
		white-space: nowrap;
	}
	thead th {
		background: var(--color-surface-muted);
		font-weight: 600;
	}
	tbody th {
		font-weight: 500;
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.best {
		font-weight: 700;
	}
	.climatology {
		color: var(--color-text-muted);
	}
	tbody tr:last-child td,
	tbody tr:last-child th {
		border-bottom: 0;
	}
	figcaption {
		margin: 0;
		font-size: 0.74rem;
		color: var(--color-text-dim);
	}
</style>
