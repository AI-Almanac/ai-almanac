<script lang="ts">
	import { untrack } from 'svelte';
	import type { Job, JobMetrics, MetricDefinition } from '$lib/api';
	import { getCachedJobMetrics } from '$lib/benchmarks.svelte';
	import {
		loadMetricDefinitions,
		metricMap,
		metricOptions,
		windowLabel
	} from '$lib/metric-metadata';
	import { modelDisplayName } from '$lib/model-names';
	import MetricsTable from './MetricsTable.svelte';
	import MetricMap from './MetricMap.svelte';
	import SegmentedTabs, { type SegmentedTabOption } from './SegmentedTabs.svelte';
	import AllMetricsPanel from './AllMetricsPanel.svelte';

	type Props = { jobs: Job[] };
	let { jobs }: Props = $props();

	// The tab boundary is map vs. everything-else, not spatial vs. probabilistic:
	// the All Metrics panel covers both families, so no metric is hidden behind
	// whichever tab you didn't pick. This split is interim — it keeps the new
	// metric views decoupled from the map pending the map rewrite, after which the
	// portrait should drive the map directly.
	const TABS: SegmentedTabOption[] = [
		{
			value: 'map',
			label: 'Map',
			hint: 'Per-grid-point spatial metrics'
		},
		{
			value: 'metrics',
			label: 'All Metrics',
			hint: 'Every metric by model and lead time, with skill curves'
		}
	];
	let activeTab = $state('map');

	let metricsByJob = $state<Record<string, JobMetrics>>({});
	let metricDefinitions = $state<MetricDefinition[]>([]);
	const definitionsById = $derived(metricMap(metricDefinitions));
	// Depend on the job-id set (stable across polls) and loaded metrics, not the jobs
	// array identity — the polling loop hands us a fresh array every tick even when the
	// completed jobs are unchanged, which would otherwise churn every downstream derived.
	const jobsKey = $derived(
		jobs
			.map((job) => job.id)
			.sort()
			.join(',')
	);
	const currentMetrics = $derived.by(() => {
		jobsKey;
		return untrack(() => jobs).flatMap((job) => {
			const metrics = metricsByJob[job.id];
			return metrics ? [metrics] : [];
		});
	});

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
	const metricWindowAvailability = $derived(
		currentMetrics.reduce<Record<string, string[]>>((availability, metrics) => {
			for (const windowMetrics of metrics.windows) {
				if (windowMetrics.model === 'climatology') continue;
				for (const metric of Object.keys(windowMetrics.metrics)) {
					const windows = availability[metric] ?? [];
					if (!windows.includes(windowMetrics.window)) {
						availability[metric] = [...windows, windowMetrics.window];
					}
				}
			}
			return availability;
		}, {})
	);
	const metricWindowAvailabilityByJob = $derived(
		currentMetrics.reduce<Record<string, Record<string, string[]>>>((availability, metrics) => {
			const jobAvailability = availability[metrics.job_id] ?? {};
			for (const windowMetrics of metrics.windows) {
				if (windowMetrics.model === 'climatology') continue;
				for (const metric of Object.keys(windowMetrics.metrics)) {
					const windows = jobAvailability[metric] ?? [];
					if (!windows.includes(windowMetrics.window)) {
						jobAvailability[metric] = [...windows, windowMetrics.window];
					}
				}
			}
			availability[metrics.job_id] = jobAvailability;
			return availability;
		}, {})
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

<div class="viewer" data-tour="results">
	<SegmentedTabs
		options={TABS}
		value={activeTab}
		onSelect={(value) => (activeTab = value)}
		ariaLabel="Results view"
	/>

	{#if activeTab === 'map'}
		{#if jobs.length > 0 && mapMetrics.length > 0 && windowOptions.length > 0}
			<MetricMap
				{jobs}
				forecastWindow={windowOptions[0].value}
				forecastWindows={windowOptions}
				metrics={mapMetrics}
				{metricWindowAvailability}
				{metricWindowAvailabilityByJob}
			/>
		{:else}
			<p class="empty">No spatial data available for this run set.</p>
		{/if}

		<div class="tables" data-tour="metrics-tables">
			{#each jobs as job (job.id)}
				<div class="table-section">
					<p class="table-model">{job.model_display_name || modelDisplayName(job.model_name)}</p>
					<MetricsTable jobId={job.id} />
				</div>
			{/each}
		</div>
	{:else}
		<AllMetricsPanel {jobs} />
	{/if}
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
