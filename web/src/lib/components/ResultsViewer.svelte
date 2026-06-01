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
						.filter((windowMetrics) => windowMetrics.model !== 'climatology')
						.flatMap((windowMetrics) => Object.keys(windowMetrics.metrics))
				)
			),
			definitionsById
		)
	);

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

</script>

<div class="viewer">
	{#if jobs.length > 0 && mapMetrics.length > 0 && windowOptions.length > 0}
		<MetricMap
			{jobs}
			forecastWindow={windowOptions[0].value}
			forecastWindows={windowOptions}
			metrics={mapMetrics}
		/>
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
