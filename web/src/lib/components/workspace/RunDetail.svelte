<script lang="ts">
	import { failureMessage, getJob, type Job, type WorkspaceArtifact } from '$lib/api';
	import AllMetricsPanel from '$lib/components/AllMetricsPanel.svelte';
	import ResultsViewer from '$lib/components/ResultsViewer.svelte';
	import BlendResults from '$lib/components/blend/BlendResults.svelte';
	import BlendForecastMap from '$lib/components/BlendForecastMap.svelte';
	import SegmentedTabs, { type SegmentedTabOption } from '$lib/components/SegmentedTabs.svelte';
	import {
		areaExport,
		BLEND_STAGE_VIEW,
		headlineModelJobId
	} from '$lib/components/metric-map/stage-map';
	import { modelProgress, needsProgress } from '$lib/workspace/run-lifecycle';
	import { finishedJobs, jobKind, type Run } from '$lib/workspace/runs';
	import FileFamilies from './FileFamilies.svelte';
	import RunProgress from './RunProgress.svelte';
	import SkillByArea from './SkillByArea.svelte';

	const MODELS_VIEW: SegmentedTabOption = {
		value: 'models',
		label: 'Models and map',
		hint: 'The models side by side, then the map'
	};
	const ZONES_VIEW: SegmentedTabOption = {
		value: 'zones',
		label: 'By zone',
		hint: 'Each model’s skill in each area'
	};
	const METRICS_VIEW: SegmentedTabOption = {
		value: 'metrics',
		label: 'All metrics',
		hint: 'Every metric by model and lead time, with skill curves'
	};

	interface Props {
		run: Run;
		/** The workspace the run is in; the by-zone view needs it to ask for its table. */
		workspaceId?: string;
		onOpenFile: (file: WorkspaceArtifact) => void;
		onDownload: (file: WorkspaceArtifact) => void;
		onRenameFile?: (file: WorkspaceArtifact, title: string | null) => Promise<void>;
		/** The lead-time window the skill maps open on, e.g. from a metrics file. */
		initialWindow?: string;
	}

	let { run, workspaceId, onOpenFile, onDownload, onRenameFile, initialWindow }: Props = $props();

	let benchmarkView = $state('models');
	// Skill by zone is worked out for a workspace, so a run shown outside one has no such view.
	const benchmarkViews = $derived(
		workspaceId ? [MODELS_VIEW, ZONES_VIEW, METRICS_VIEW] : [MODELS_VIEW, METRICS_VIEW]
	);
	const mapOffstage = $derived(benchmarkView !== 'models');

	const areaOfInterest = $derived(
		run.jobs.find((job) => job.area_of_interest?.length)?.area_of_interest ?? null
	);
	// A finished run with nothing wrong says so beside its title; anything else is spelled out.
	const showProgress = $derived(needsProgress(modelProgress(run, 0)));

	const completeBenchmarkIds = $derived(
		finishedJobs(run, 'benchmark')
			.map((job) => job.id)
			.sort()
			.join(',')
	);
	const finishedBlends = $derived(finishedJobs(run, 'blend'));
	const finishedForecasts = $derived(finishedJobs(run, 'forecast'));

	function blendSummaryReady(jobId: string): boolean {
		return run.files.some(
			(file) => file.job_id === jobId && file.label.startsWith('summary_models_pooled')
		);
	}

	let jobs = $state<Job[]>([]);
	let loadError = $state<string | null>(null);
	const benchmarkJobs = $derived(run.jobs.filter((job) => jobKind(job) === 'benchmark'));

	// Keyed on the id string so live workspace updates do not refetch unchanged jobs.
	$effect(() => {
		const ids = completeBenchmarkIds ? completeBenchmarkIds.split(',') : [];
		let cancelled = false;
		Promise.all(ids.map((id) => getJob(id)))
			.then((loaded) => {
				if (cancelled) return;
				jobs = loaded;
				loadError = null;
			})
			.catch((e) => {
				if (!cancelled) loadError = failureMessage(e);
			});
		return () => {
			cancelled = true;
		};
	});
</script>

<div class="run-detail">
	{#if showProgress}
		{#key run.key}<RunProgress {run} />{/key}
	{/if}

	{#if loadError}
		<p class="error">Could not load these results: {loadError}</p>
	{:else if jobs.length > 0}
		<div class="viewer">
			<SegmentedTabs
				options={benchmarkViews}
				value={benchmarkView}
				onSelect={(value) => (benchmarkView = value)}
				ariaLabel="Benchmark view"
			/>
			{#if workspaceId && benchmarkView === 'zones'}
				<SkillByArea
					{workspaceId}
					runKey={run.key}
					{initialWindow}
					finishedKey={completeBenchmarkIds}
					jobs={benchmarkJobs}
				/>
			{:else if benchmarkView === 'metrics'}
				<AllMetricsPanel {jobs} />
			{/if}
			<!-- Kept while another view is shown, so the map comes back as the reader left it. -->
			<div
				class="viewer"
				class:offstage={mapOffstage}
				inert={mapOffstage}
				aria-hidden={mapOffstage}
			>
				<ResultsViewer
					{jobs}
					{initialWindow}
					stage={{ areaOfInterest, bestModelJobId: headlineModelJobId(benchmarkJobs) }}
					shown={!mapOffstage}
					onShowAllMetrics={() => (benchmarkView = 'metrics')}
				/>
			</div>
		</div>
	{/if}

	{#each finishedForecasts as job (job.id)}
		<div class="viewer forecast-map">
			<BlendForecastMap
				jobId={job.id}
				regionId={job.region_id ?? null}
				initialView={BLEND_STAGE_VIEW}
				exportContext={areaExport(job)}
			/>
		</div>
	{/each}

	{#each finishedBlends as job (job.id)}
		<div class="viewer">
			<BlendResults
				jobId={job.id}
				summaryReady={blendSummaryReady(job.id)}
				mapView={BLEND_STAGE_VIEW}
				mapExport={areaExport(job)}
			/>
		</div>
	{/each}

	<section class="files">
		<h3>Files</h3>
		{#if run.files.length === 0}
			<p class="muted">No files yet.</p>
		{:else}
			<FileFamilies files={run.files} onOpen={onOpenFile} {onDownload} onRename={onRenameFile} />
		{/if}
	</section>
</div>

<style>
	.run-detail {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		min-width: 0;
	}
	.viewer {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		min-width: 0;
	}
	/* Out of sight, out of the flow, and still laid out at its real size, so the map
	   inside keeps its canvas and camera. */
	.offstage {
		position: absolute;
		inset: 0;
		overflow: hidden;
		visibility: hidden;
		pointer-events: none;
	}
	/* The map fills its host, so the host sets its shape, as on the Forecasts page. */
	.forecast-map {
		position: relative;
		width: 100%;
		aspect-ratio: 4 / 3;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		overflow: hidden;
	}
	.files {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	h3 {
		margin: 0;
		padding: 0 0.4rem 0.3rem;
		border-bottom: 1px solid var(--color-border);
		font-size: var(--text-size-caption);
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--color-text-muted);
	}
	.muted {
		margin: 0;
		padding: 0 0.4rem;
		font-size: 0.84rem;
		color: var(--color-text-muted);
	}
	.error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.86rem;
	}
</style>
