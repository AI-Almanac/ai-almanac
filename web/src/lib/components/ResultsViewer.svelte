<script lang="ts">
	import type { Job } from '$lib/api';
	import MetricsTable from './MetricsTable.svelte';
	import MetricMap from './MetricMap.svelte';

	type Props = { jobs: Job[] };
	let { jobs }: Props = $props();

	const MAP_METRICS = [
		{ value: 'false_alarm_rate', label: 'False alarm rate' },
		{ value: 'miss_rate', label: 'Miss rate' },
		{ value: 'mean_mae', label: 'Mean absolute error' }
	];

	function modelDisplayName(modelName: string): string {
		const labels: Record<string, string> = {
			fuxi: 'FuXi',
			aifs: 'AIFS',
			aifs_daily: 'AIFS Daily',
			fuxi_s2s: 'FuXi S2S',
			climatology: 'Climatology'
		};
		return labels[modelName.toLowerCase()] ?? modelName;
	}

	const WINDOW_OPTS: { value: '1-15' | '16-30'; label: string }[] = [
		{ value: '1-15', label: 'Days 1–15' },
		{ value: '16-30', label: 'Days 16–30' }
	];

	let mapWindow = $state<'1-15' | '16-30'>('1-15');
</script>

<div class="viewer">
	<div class="filter-row">
		{#each WINDOW_OPTS as opt}
			<button
				class="chip"
				class:active={mapWindow === opt.value}
				onclick={() => {
					mapWindow = opt.value;
				}}>{opt.label}</button
			>
		{/each}
	</div>

	{#if jobs.length > 0}
		<MetricMap {jobs} forecastWindow={mapWindow} metrics={MAP_METRICS} />
	{:else}
		<p class="empty">No spatial data available for this run set.</p>
	{/if}

	<div class="tables">
		{#each jobs as job (job.id)}
			<div class="table-section">
				<p class="table-model">{modelDisplayName(job.model_name)}</p>
				<MetricsTable jobId={job.id} />
			</div>
		{/each}
	</div>
</div>

<style>
	.viewer {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.filter-row {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}

	.chip {
		padding: 0.3rem 0.75rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 1rem;
		background: var(--color-surface);
		color: var(--color-text-dim);
		font-size: 0.75rem;
		font-weight: 500;
		cursor: pointer;
		transition:
			background-color 0.12s,
			color 0.12s,
			border-color 0.12s;
	}
	.chip:hover {
		border-color: var(--color-accent);
		color: var(--color-accent);
	}
	.chip.active {
		background: var(--color-accent-light);
		border-color: var(--color-accent);
		color: var(--color-accent);
	}

	.empty {
		color: var(--color-text-dim);
		font-size: 0.85rem;
		margin: 0;
		padding: 1rem 0;
	}

	.tables {
		display: flex;
		flex-direction: column;
		gap: 1.5rem;
		margin-top: 0.5rem;
	}

	.table-section {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.table-model {
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--color-text-dim);
		margin: 0;
	}
</style>
