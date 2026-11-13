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
	import { compareWindows } from '$lib/metric-comparison';
	import { modelDisplayName } from '$lib/model-names';
	import type { MapInitialView } from '$lib/components/metric-map/initialView';
	import {
		areaWords,
		shouldRepickView,
		stageMapView,
		yearsWords
	} from '$lib/components/metric-map/stage-map';
	import MetricsTable from './MetricsTable.svelte';
	import SkillScoresTable from './SkillScoresTable.svelte';
	import MetricMap from './MetricMap.svelte';
	import SegmentedTabs, { type SegmentedTabOption } from './SegmentedTabs.svelte';
	import AllMetricsPanel from './AllMetricsPanel.svelte';
	import ModelComparisonTable from './ModelComparisonTable.svelte';

	/** `initialWindow` opens the map on that lead-time window when the run has it. */
	/**
	 * `stage` is a workspace's benchmark stage: no tabs, the comparison table leads, and
	 * the map opens on onset error for the best model over the areas the run covered, with
	 * an export. Left out, the viewer behaves as on the Benchmarks page.
	 */
	type Props = {
		jobs: Job[];
		initialWindow?: string;
		stage?: {
			areaOfInterest: string[] | null;
			/** The job of the model the run's headline names as its best, when it is known. */
			bestModelJobId?: string | null;
		};
		/** False while the viewer is kept but out of sight; the map re-measures when it returns. */
		shown?: boolean;
		/** Where a host with views of its own shows every metric; without it, the viewer's own tab opens. */
		onShowAllMetrics?: () => void;
	};
	let {
		jobs,
		initialWindow,
		stage,
		shown = true,
		onShowAllMetrics = () => (activeTab = 'metrics')
	}: Props = $props();

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

	// Probabilistic runs write region-pooled scores only, so they have nothing to map.
	const spatialJobIds = $derived(
		new Set(
			currentMetrics
				.filter((metrics) => metrics.windows.some((window) => window.model !== 'climatology'))
				.map((metrics) => metrics.job_id)
		)
	);
	const spatialJobs = $derived(jobs.filter((job) => spatialJobIds.has(job.id)));
	const pooledOnlyJobs = $derived(
		jobs.filter((job) => job.params?.probabilistic && !spatialJobIds.has(job.id))
	);
	const climatologyJobIds = $derived(
		currentMetrics
			.filter((metrics) => metrics.windows.some((window) => window.model === 'climatology'))
			.map((metrics) => metrics.job_id)
	);

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
			.sort(compareWindows)
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

	// The stage map opens on the best model, which is only known once every model's
	// scores are in; a model whose scores cannot be read must not hold the map back.
	let unreadableMetrics = $state<Record<string, true>>({});
	const metricsSettled = $derived(
		jobs.every(
			(job) => job.status !== 'complete' || metricsByJob[job.id] || unreadableMetrics[job.id]
		)
	);

	// The stage map's opening view, and the finished models it was picked for. The
	// map is drawn afresh when it changes, so it changes only while the reader has
	// left the map as it opened.
	let stageView = $state.raw<{ finishedModels: string; view: MapInitialView } | null>(null);
	let mapChangedByHand = $state(false);
	const mapReady = $derived(!stage || stageView !== null);

	$effect(() => {
		if (!stage || !metricsSettled) return;
		const finishedModels = jobsKey;
		untrack(() => {
			if (!shouldRepickView(stageView?.finishedModels ?? null, finishedModels, mapChangedByHand))
				return;
			stageView = {
				finishedModels,
				view: stageMapView(spatialJobs, currentMetrics, initialWindow, stage.bestModelJobId)
			};
		});
	});

	$effect(() => {
		for (const job of jobs) {
			if (job.status !== 'complete' || metricsByJob[job.id]) continue;
			getCachedJobMetrics(job.id).then(
				(metrics) => {
					metricsByJob = { ...metricsByJob, [job.id]: metrics };
				},
				() => {
					unreadableMetrics = { ...unreadableMetrics, [job.id]: true };
				}
			);
		}
	});
</script>

<div class="viewer" data-tour="results">
	{#if !stage}
		<SegmentedTabs
			options={TABS}
			value={activeTab}
			onSelect={(value) => (activeTab = value)}
			ariaLabel="Results view"
		/>
	{/if}

	{#if stage || activeTab === 'map'}
		{#if stage}
			<ModelComparisonTable
				{jobs}
				metrics={currentMetrics}
				definitions={metricDefinitions}
				{initialWindow}
				baselineCompetes
			/>
		{/if}
		{#if !mapReady}
			<p class="empty">Loading the map…</p>
		{:else if spatialJobs.length > 0 && mapMetrics.length > 0 && windowOptions.length > 0}
			{#key stageView?.finishedModels}
				<MetricMap
					jobs={spatialJobs}
					forecastWindow={windowOptions.find((option) => option.value === initialWindow)?.value ??
						windowOptions[0].value}
					forecastWindows={windowOptions}
					metrics={mapMetrics}
					{metricWindowAvailability}
					{metricWindowAvailabilityByJob}
					{climatologyJobIds}
					initialView={stage ? stageView?.view : undefined}
					exportContext={stage
						? {
								area: areaWords(stage.areaOfInterest, spatialJobs[0].region_name),
								years: yearsWords(spatialJobs)
							}
						: undefined}
					onUserChange={() => (mapChangedByHand = true)}
					{shown}
				/>
			{/key}
		{:else if pooledOnlyJobs.length === 0}
			<p class="empty">No spatial data available for this run set.</p>
		{/if}
		{#if pooledOnlyJobs.length > 0}
			<p class="empty">
				No per-grid-point output for probabilistic runs ({pooledOnlyJobs
					.map((job) => job.model_display_name || modelDisplayName(job.model_name))
					.join(', ')}) — see
				<button class="link" onclick={onShowAllMetrics}>All Metrics</button>.
			</p>
		{/if}

		{#if !stage}
			<div class="tables" data-tour="metrics-tables">
				{#each jobs as job (job.id)}
					<div class="table-section">
						<p class="table-model">{job.model_display_name || modelDisplayName(job.model_name)}</p>
						{#if job.params?.probabilistic}
							<SkillScoresTable jobId={job.id} />
						{/if}
						{#if !pooledOnlyJobs.includes(job)}
							<MetricsTable jobId={job.id} />
						{/if}
					</div>
				{/each}
			</div>
		{/if}
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

	.link {
		background: none;
		border: none;
		padding: 0;
		font: inherit;
		color: var(--color-accent);
		text-decoration: underline;
		cursor: pointer;
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
