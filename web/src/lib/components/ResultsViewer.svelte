<script lang="ts">
	import type { Job, JobMetrics, MetricDefinition } from '$lib/api';
	import { getCachedJobMetrics } from '$lib/benchmarks.svelte';
	import {
		loadMetricDefinitions,
		metricMap,
		metricOptions,
		windowLabel
	} from '$lib/metric-metadata';
	import MetricsTable from './MetricsTable.svelte';
	import MetricMap from './MetricMap.svelte';

	type Props = { jobs: Job[] };
	let { jobs }: Props = $props();

	let metricsByJob = $state<Record<string, JobMetrics>>({});
	let metricDefinitions = $state<MetricDefinition[]>([]);
	const definitionsById = $derived(metricMap(metricDefinitions));
	const currentMetrics = $derived(
		jobs.flatMap((job) => {
			const metrics = metricsByJob[job.id];
			return metrics ? [metrics] : [];
		})
	);

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

	function windowSortValue(window: string): number {
		if (window === '1-15') return 0;
		if (window === '16-30') return 1;
		if (window === 'all') return 2;
		return 10;
	}

	const windowOptions = $derived(
		[
			...new Set(
				currentMetrics.flatMap((metrics) =>
					metrics.windows
						.filter((window) => window.model !== 'climatology')
						.map((window) => window.window)
				)
			)
		]
			.sort((a, b) => windowSortValue(a) - windowSortValue(b) || a.localeCompare(b))
			.map((window) => ({ value: window, label: windowLabel(window) }))
	);

	const mapMetrics = $derived(
		metricOptions(
			new Set(
				currentMetrics.flatMap((metrics) =>
					metrics.windows
						.filter((window) => window.model !== 'climatology' && window.window === mapWindow)
						.flatMap((window) => Object.keys(window.metrics))
				)
			),
			definitionsById
		)
	);

	let mapWindow = $state('1-15');

	$effect(() => {
		loadMetricDefinitions().then((definitions) => {
			metricDefinitions = definitions;
		});
	});

	$effect(() => {
		for (const job of jobs) {
			if (job.status !== 'complete' || metricsByJob[job.id]) continue;
			getCachedJobMetrics(job.id).then((metrics) => {
				metricsByJob = { ...metricsByJob, [job.id]: metrics };
			});
		}
	});

	$effect(() => {
		if (windowOptions.length === 0) return;
		if (!windowOptions.some((option) => option.value === mapWindow)) {
			mapWindow = windowOptions[0].value;
		}
	});
</script>

<div class="viewer">
	<div class="filter-row">
		{#each windowOptions as opt}
			<button
				class="chip"
				class:active={mapWindow === opt.value}
				onclick={() => {
					mapWindow = opt.value;
				}}>{opt.label}</button
			>
		{/each}
	</div>

	{#if jobs.length > 0 && mapMetrics.length > 0}
		<MetricMap {jobs} forecastWindow={mapWindow} metrics={mapMetrics} />
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
