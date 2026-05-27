<script lang="ts">
	import {
		getJobResults,
		getJobMetrics,
		type Job,
		type JobResult,
		type JobMetrics,
		type WindowMetrics,
		type MetricDefinition
	} from '$lib/api';
	import { fetchResultBlob } from '$lib/benchmarks.svelte';
	import {
		formatMetricDelta,
		formatMetricValue,
		isAnnualMaeMetric,
		loadMetricDefinitions,
		lowerIsBetter,
		metricLabel,
		metricMap,
		metricUnit,
		orderMetricKeys,
		windowLabel
	} from '$lib/metric-metadata';
	import { parseResult, type ParsedFigure } from '$lib/result-parser';
	import FigureLightbox from './FigureLightbox.svelte';

	type Props = { jobs: Job[] };
	let { jobs }: Props = $props();

	// --- Per-job data fetching ---

	type JobData = {
		results: JobResult[] | null;
		metrics: JobMetrics | null;
		figures: ParsedFigure[];
		error: string | null;
		loading: boolean;
	};

	let jobData = $state<Record<string, JobData>>({});
	let metricDefinitions = $state<MetricDefinition[]>([]);
	const definitionsById = $derived(metricMap(metricDefinitions));

	$effect(() => {
		for (const job of jobs) {
			if (job.status !== 'complete') continue;
			if (jobData[job.id]) continue;
			// Initialise entry immediately so we don't double-fetch
			jobData[job.id] = { results: null, metrics: null, figures: [], error: null, loading: true };
			Promise.all([getJobResults(job.id), getJobMetrics(job.id)])
				.then(([results, metrics]) => {
					jobData[job.id] = {
						results,
						metrics,
						figures: results.filter((r) => r.type === 'figure').map(parseResult),
						error: null,
						loading: false
					};
				})
				.catch((e) => {
					jobData[job.id] = {
						results: null,
						metrics: null,
						figures: [],
						error: e.message,
						loading: false
					};
				});
		}
	});

	// --- Metrics comparison ---

	$effect(() => {
		loadMetricDefinitions().then((definitions) => {
			metricDefinitions = definitions;
		});
	});

	// Collect all unique window labels across all jobs
	const allWindows = $derived(
		[
			...new Set(jobs.flatMap((j) => jobData[j.id]?.metrics?.windows.map((w) => w.window) ?? []))
		].sort()
	);

	function getWindow(jobId: string, window: string): WindowMetrics | undefined {
		return jobData[jobId]?.metrics?.windows.find(
			(w) => w.window === window && w.model !== 'climatology'
		);
	}

	function metricKeysForWindow(window: string): string[] {
		const sets = jobs
			.map((job) => getWindow(job.id, window))
			.filter((win): win is WindowMetrics => Boolean(win))
			.map((win) => new Set(Object.keys(win.metrics).filter((key) => !isAnnualMaeMetric(key))));
		if (sets.length === 0) return [];
		const [first, ...rest] = sets;
		return orderMetricKeys(
			[...first].filter((key) => rest.every((set) => set.has(key))),
			definitionsById
		);
	}

	function deltaClass(metric: string, delta: number): string {
		if (Math.abs(delta) < 0.005) return 'delta-neutral';
		const preference = lowerIsBetter(metric, definitionsById);
		if (preference == null) return 'delta-neutral';
		return preference ? (delta < 0 ? 'delta-better' : 'delta-worse') : delta > 0 ? 'delta-better' : 'delta-worse';
	}

	// --- Figure comparison ---

	// Unique (kind, window) pairs across all jobs, sorted
	const figurePairs = $derived(
		[
			...new Map(
				jobs.flatMap((j) =>
					(jobData[j.id]?.figures ?? [])
						.filter((f) => f.kind === 'spatial_metrics')
						.map((f) => [
							`${f.kind}|${f.window}`,
							{ kind: f.kind, window: f.window, label: `Spatial — Days ${f.window}` }
						])
				)
			).values()
		].sort((a, b) => (a.window ?? '').localeCompare(b.window ?? ''))
	);

	function figureFor(jobId: string, window: string | null): ParsedFigure | undefined {
		return jobData[jobId]?.figures.find((f) => f.kind === 'spatial_metrics' && f.window === window);
	}

	// Portrait figures
	const portraitPairs = $derived([
		...new Map(
			jobs.flatMap((j) =>
				(jobData[j.id]?.figures ?? [])
					.filter((f) => f.kind === 'portrait')
					.map((f) => [f.label, { label: f.label }])
			)
		).values()
	]);

	function portraitFor(jobId: string, label: string): ParsedFigure | undefined {
		return jobData[jobId]?.figures.find((f) => f.kind === 'portrait' && f.label === label);
	}

	// --- Lightbox ---
	let lightboxFigs = $state<ParsedFigure[]>([]);
	let lightboxIndex = $state(0);
	let lightboxOpen = $state(false);

	function openLightbox(figs: ParsedFigure[], idx: number) {
		lightboxFigs = figs;
		lightboxIndex = idx;
		lightboxOpen = true;
	}

	// Helper: collect figures in a row across all jobs for lightbox cycling
	function rowFigs(getFig: (id: string) => ParsedFigure | undefined): ParsedFigure[] {
		return jobs.map((j) => getFig(j.id)).filter((f): f is ParsedFigure => f != null);
	}
</script>

<div class="panel">
	<!-- ── Metrics comparison ── -->
	{#if allWindows.length > 0}
		<section class="section">
			<h2 class="section-heading">Metrics Comparison</h2>
			{#each allWindows as win}
				{@const windowMetrics = metricKeysForWindow(win)}
				<div class="win-block">
					<p class="win-label">{windowLabel(win)}</p>
					<div class="table-wrap">
						<table>
							<thead>
								<tr>
									<th class="metric-col">Metric</th>
									{#each jobs as job, i}
										<th class="job-col">
											<span class="job-col-model">{job.model_name.toUpperCase()}</span>
											<span class="job-col-dates"
												>{job.params?.start_date ?? ''} – {job.params?.end_date ?? ''}</span
											>
										</th>
										{#if i > 0}<th class="delta-col">Δ vs #1</th>{/if}
									{/each}
								</tr>
							</thead>
							<tbody>
								{#each windowMetrics as varKey}
									{@const baseWin = getWindow(jobs[0].id, win)}
									{@const baseStat = baseWin?.metrics[varKey]}
									<tr>
										<td class="metric-name">{metricLabel(varKey, definitionsById)}</td>
										{#each jobs as job, i}
											{@const w = getWindow(job.id, win)}
											{@const s = w?.metrics[varKey]}
											{@const unit = metricUnit(varKey, s?.unit ?? baseStat?.unit, definitionsById)}
											<td class="val">
												{#if jobData[job.id]?.loading}
													<span class="loading-dot">…</span>
												{:else if s}
													{formatMetricValue(s.mean, unit)}
												{:else}
													<span class="na">—</span>
												{/if}
											</td>
											{#if i > 0}
												<td class="delta">
													{#if s && baseStat}
														{@const d = s.mean - baseStat.mean}
														<span class={deltaClass(varKey, d)}>
															{formatMetricDelta(d, unit)}
														</span>
													{:else}
														<span class="na">—</span>
													{/if}
												</td>
											{/if}
										{/each}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>
			{/each}
		</section>
	{/if}

	<!-- ── Spatial figure grid ── -->
	{#if figurePairs.length > 0}
		<section class="section">
			<h2 class="section-heading">Spatial Maps</h2>
			<div class="fig-grid" style="--ncols: {jobs.length}">
				<!-- Column headers -->
				<div class="fig-row-label"></div>
				{#each jobs as job}
					<div class="fig-col-header">
						<span class="job-col-model">{job.model_name.toUpperCase()}</span>
						<span class="job-col-dates"
							>{job.params?.start_date ?? ''} – {job.params?.end_date ?? ''}</span
						>
					</div>
				{/each}

				{#each figurePairs as pair}
					<div class="fig-row-label">{pair.label}</div>
					{#each jobs as job, ji}
						{@const fig = figureFor(job.id, pair.window)}
						{@const figs = rowFigs((id) => figureFor(id, pair.window))}
						<button
							class="fig-cell"
							disabled={!fig}
							onclick={() => fig && openLightbox(figs, figs.indexOf(fig))}
							title={fig ? 'Click to expand' : ''}
							aria-label={fig ? `Expand ${fig.label}` : `${pair.label} not available`}
						>
							{#if jobData[job.id]?.loading}
								<div class="cell-placeholder loading-dot">Loading…</div>
							{:else if fig}
								{#await fetchResultBlob(fig.raw.url)}
									<div class="cell-placeholder">Loading…</div>
								{:then src}
									<img {src} alt={fig.label} />
								{:catch}
									<div class="cell-placeholder error">Failed</div>
								{/await}
							{:else}
								<div class="cell-placeholder na">Not available</div>
							{/if}
						</button>
					{/each}
				{/each}
			</div>
		</section>
	{/if}

	<!-- ── Portrait heatmaps ── -->
	{#if portraitPairs.length > 0}
		<section class="section">
			<h2 class="section-heading">Portrait Heatmaps</h2>
			<div class="fig-grid" style="--ncols: {jobs.length}">
				<div class="fig-row-label"></div>
				{#each jobs as job}
					<div class="fig-col-header">
						<span class="job-col-model">{job.model_name.toUpperCase()}</span>
					</div>
				{/each}
				{#each portraitPairs as pair}
					<div class="fig-row-label">{pair.label}</div>
					{#each jobs as job}
						{@const fig = portraitFor(job.id, pair.label)}
						{@const figs = rowFigs((id) => portraitFor(id, pair.label))}
						<button
							class="fig-cell"
							disabled={!fig}
							onclick={() => fig && openLightbox(figs, figs.indexOf(fig))}
							aria-label={fig ? `Expand ${fig.label}` : `${pair.label} not available`}
						>
							{#if jobData[job.id]?.loading}
								<div class="cell-placeholder">Loading…</div>
							{:else if fig}
								{#await fetchResultBlob(fig.raw.url)}
									<div class="cell-placeholder">Loading…</div>
								{:then src}
									<img {src} alt={fig.label} />
								{:catch}
									<div class="cell-placeholder error">Failed</div>
								{/await}
							{:else}
								<div class="cell-placeholder na">Not available</div>
							{/if}
						</button>
					{/each}
				{/each}
			</div>
		</section>
	{/if}

	{#if jobs.every((j) => j.status !== 'complete')}
		<p class="empty">Select completed jobs to compare their results.</p>
	{/if}
</div>

{#if lightboxOpen}
	<FigureLightbox
		figures={lightboxFigs}
		bind:index={lightboxIndex}
		onclose={() => {
			lightboxOpen = false;
		}}
	/>
{/if}

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: 2rem;
	}

	.section {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.section-heading {
		font-size: 0.65rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--color-text-dim);
		margin: 0;
	}

	/* ── Metrics table ── */
	.win-block {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.win-label {
		font-size: 0.68rem;
		font-weight: 600;
		color: var(--color-accent);
		margin: 0;
		text-transform: uppercase;
		letter-spacing: 0.06em;
	}

	.table-wrap {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8rem;
	}

	th {
		padding: 0.4rem 0.75rem;
		text-align: right;
		font-size: 0.65rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--color-text-dim);
		border-bottom: 1px solid var(--color-border-subtle);
		white-space: nowrap;
	}

	th.metric-col {
		text-align: left;
	}
	th.job-col {
		min-width: 110px;
	}
	th.delta-col {
		min-width: 70px;
		color: var(--color-text-dim);
		opacity: 0.7;
	}

	.job-col-model {
		display: block;
		font-size: 0.7rem;
		font-weight: 700;
		color: var(--color-text-muted);
		text-transform: uppercase;
	}

	.job-col-dates {
		display: block;
		font-size: 0.6rem;
		font-weight: 400;
		text-transform: none;
		letter-spacing: 0;
		color: var(--color-text-dim);
		font-family: var(--font-mono);
	}

	td {
		padding: 0.4rem 0.75rem;
		text-align: right;
		font-family: var(--font-mono);
		font-size: 0.78rem;
		border-bottom: 1px solid var(--color-border-subtle);
		white-space: nowrap;
		color: var(--color-text);
	}

	td.metric-name {
		text-align: left;
		font-family: var(--font-body);
		font-weight: 500;
		color: var(--color-text-muted);
	}

	td.val {
		font-weight: 500;
	}
	td.delta {
		font-size: 0.73rem;
	}

	.delta-better {
		color: #4caf78;
	}
	.delta-worse {
		color: #e07070;
	}
	.delta-neutral {
		color: var(--color-text-dim);
	}

	.na {
		color: var(--color-text-dim);
		opacity: 0.5;
	}
	.loading-dot {
		color: var(--color-text-dim);
		font-style: italic;
	}

	tbody tr:last-child td {
		border-bottom: none;
	}
	tbody tr:hover td {
		background: var(--color-accent-glow);
	}

	/* ── Figure grid ── */
	.fig-grid {
		display: grid;
		grid-template-columns: 120px repeat(var(--ncols), minmax(200px, 1fr));
		gap: 0.5rem;
		overflow-x: auto;
	}

	.fig-col-header {
		padding: 0.4rem 0.5rem;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}

	.fig-row-label {
		display: flex;
		align-items: center;
		font-size: 0.68rem;
		font-weight: 500;
		color: var(--color-text-dim);
		padding: 0.25rem 0.25rem 0.25rem 0;
		line-height: 1.3;
	}

	.fig-cell {
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
		overflow: hidden;
		background: var(--color-bg);
		cursor: zoom-in;
		min-height: 80px;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0;
		color: inherit;
		font: inherit;
	}

	.fig-cell:disabled {
		cursor: default;
	}

	.fig-cell img {
		width: 100%;
		display: block;
	}

	.cell-placeholder {
		padding: 1rem;
		font-size: 0.75rem;
		color: var(--color-text-dim);
		text-align: center;
	}

	.cell-placeholder.na {
		font-style: italic;
		opacity: 0.6;
	}
	.cell-placeholder.error {
		color: var(--color-danger);
	}

	.empty {
		color: var(--color-text-dim);
		font-size: 0.875rem;
		margin: 0;
	}
</style>
