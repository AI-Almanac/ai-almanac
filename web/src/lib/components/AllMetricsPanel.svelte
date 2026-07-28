<script lang="ts">
	/**
	 * The non-map half of the benchmark results: every metric in one table, plus
	 * a lead-time chart for whichever row is selected.
	 *
	 * Deliberately covers **both** metric families rather than only the
	 * probabilistic ones. The tab boundary is map vs. everything-else, not spatial
	 * vs. probabilistic — splitting the families across tabs would make whichever
	 * tab you landed on the metric you reason from, which is the over-indexing
	 * failure this panel exists to prevent.
	 *
	 * Interim structure. Once the metric map is rewritten, the portrait should
	 * drive the map directly and this tab split can go away.
	 */
	import { untrack } from 'svelte';
	import type { Job, JobMetrics, JobSkillScores, MetricDefinition } from '$lib/api';
	import { getCachedJobMetrics, getCachedJobSkillScores } from '$lib/benchmarks.svelte';
	import { loadMetricDefinitions, metricMap } from '$lib/metric-metadata';
	import { modelColor } from '$lib/chart-colors';
	import { modelDisplayName } from '$lib/model-names';
	import { buildPortrait, portraitWindows } from '$lib/metric-portrait';
	import {
		collectLeadBins,
		hasMetric,
		seriesValues,
		type SkillCurveSeries,
		type SkillMetricKey
	} from '$lib/skill-series';
	import MetricPortrait from './MetricPortrait.svelte';
	import SkillCurveChart from './SkillCurveChart.svelte';

	type Props = { jobs: Job[] };
	let { jobs }: Props = $props();

	let metricsByJob = $state<Record<string, JobMetrics>>({});
	let skillByJob = $state<Record<string, JobSkillScores>>({});
	let metricDefinitions = $state<MetricDefinition[]>([]);
	let loading = $state(true);
	let fetchError = $state<string | null>(null);
	let selectedMetric = $state<string | null>(null);

	const definitionsById = $derived(metricMap(metricDefinitions));

	// Depend on the job-id set, not the array identity — the benchmarks page hands
	// us a fresh array every 3s poll tick even when nothing changed.
	const jobsKey = $derived(
		jobs
			.map((job) => job.id)
			.sort()
			.join(',')
	);

	const models = $derived.by(() => {
		jobsKey;
		return untrack(() => jobs).map((job) => ({
			key: job.id,
			model: job.model_name,
			label: job.model_display_name || modelDisplayName(job.model_name)
		}));
	});

	/** Only the run set on screen — the caches are never pruned across groups. */
	const currentScores = $derived(
		models.map((model) => skillByJob[model.key]).filter((scores) => scores != null)
	);

	const windows = $derived(
		portraitWindows(
			metricsByJob,
			skillByJob,
			models.map((model) => model.key)
		)
	);

	const portrait = $derived(
		buildPortrait({
			windows,
			models,
			metricsByJob,
			skillByJob,
			definitions: definitionsById
		})
	);

	/**
	 * Which bin field a metric's curve reads. Only these three have per-bin values
	 * in ROMP's binned CSV; the Ranked Probability Score is cumulative by
	 * construction and ROMP writes N/A per bin.
	 */
	const CURVE_FIELD: Record<string, SkillMetricKey> = {
		brier_skill_score: 'brier_skill_score',
		auc: 'auc'
	};

	/** No-skill reference for the chart's dashed line. */
	const CURVE_REFERENCE: Record<string, { value: number; label: string; caption: string }> = {
		brier_skill_score: {
			value: 0,
			label: 'No skill versus climatology',
			caption:
				'Skill relative to a climatology forecast. Above the dashed line beats climatology; below it is worse. Click a model to toggle it; hover for exact values.'
		},
		auc: {
			value: 0.5,
			label: 'No discrimination',
			caption:
				'Ability to tell onset from no-onset. 0.5 is no better than chance, 1.0 is perfect. Click a model to toggle it; hover for exact values.'
		}
	};

	const selectedRow = $derived(portrait.rows.find((row) => row.metric === selectedMetric) ?? null);
	const curveField = $derived(selectedMetric ? CURVE_FIELD[selectedMetric] : undefined);
	// Scoped to currentScores, not the whole cache: otherwise switching run sets
	// leaves the previous set's lead bins on the x axis, where every new series is
	// null and spanGaps stretches the line across phantom ticks.
	const leads = $derived(curveField ? collectLeadBins(currentScores) : []);

	const curveSeries = $derived.by((): SkillCurveSeries[] => {
		if (!curveField) return [];
		// Color from the unfiltered model index so a model missing from one metric
		// doesn't shift the others' colors between charts.
		return models
			.map((model, index) => ({
				key: model.key,
				label: model.label,
				color: modelColor(index),
				scores: skillByJob[model.key]
			}))
			.filter((entry) => entry.scores != null && hasMetric(entry.scores, curveField))
			.map((entry) => ({
				key: entry.key,
				label: entry.label,
				color: entry.color,
				values: seriesValues(entry.scores as JobSkillScores, leads, curveField)
			}));
	});

	$effect(() => {
		loadMetricDefinitions().then((definitions) => {
			metricDefinitions = definitions;
		});
	});

	// Tracks `jobs` directly: a job flipping running → complete does not change
	// the id set, so keying on jobsKey would never notice it finish. The guards
	// make the repeated poll-tick runs idempotent.
	//
	// inFlight is required, not belt-and-braces: the caches populate only after
	// their await, and `jobs` gets a fresh identity on every 3s poll tick while any
	// job in the group is still running. Without it every tick re-fires the whole
	// Promise.all against jobs already being fetched.
	const inFlight = new Set<string>();

	$effect(() => {
		const pending = jobs.filter(
			(job) =>
				job.status === 'complete' &&
				!inFlight.has(job.id) &&
				!(untrack(() => metricsByJob)[job.id] && untrack(() => skillByJob)[job.id])
		);
		if (pending.length === 0) {
			// Without this, a first run that finds nothing pending pins the panel on
			// "Loading metrics…" forever and the empty-state branch is unreachable.
			if (inFlight.size === 0) loading = false;
			return;
		}
		for (const job of pending) inFlight.add(job.id);
		loading = true;
		fetchError = null;
		Promise.all(
			pending.map(async (job) => {
				const [metrics, skill] = await Promise.all([
					getCachedJobMetrics(job.id),
					getCachedJobSkillScores(job.id)
				]);
				return [job.id, metrics, skill] as const;
			})
		)
			.then((entries) => {
				metricsByJob = {
					...untrack(() => metricsByJob),
					...Object.fromEntries(entries.map(([id, metrics]) => [id, metrics]))
				};
				skillByJob = {
					...untrack(() => skillByJob),
					...Object.fromEntries(entries.map(([id, , skill]) => [id, skill]))
				};
			})
			.catch((e) => {
				fetchError = e instanceof Error ? e.message : 'Failed to load metrics';
			})
			.finally(() => {
				for (const job of pending) inFlight.delete(job.id);
				loading = false;
			});
	});

	// Keep the stage pointed at a metric that exists, preferring one that can draw
	// a curve so the panel opens on something informative.
	//
	// Tracks `portrait`, not `selectedMetric`. Keying on selectedMetric would run
	// once at mount — while the fetch is in flight and portrait.rows is empty —
	// return early, and never re-run, leaving the stage permanently blank. The
	// write target is read through untrack so setting it doesn't re-trigger.
	//
	// This *validates* rather than only filling a null, because switching to a run
	// set that lacks the selected metric would otherwise strand the selection on a
	// row that no longer exists: no chart, no explanation, no highlighted row.
	$effect(() => {
		const rows = portrait.rows;
		if (rows.length === 0) return;
		const current = untrack(() => selectedMetric);
		if (current !== null && rows.some((row) => row.metric === current)) return;
		selectedMetric = (rows.find((row) => CURVE_FIELD[row.metric]) ?? rows[0]).metric;
	});
</script>

<div class="panel">
	{#if loading && portrait.rows.length === 0}
		<p class="loading">Loading metrics…</p>
	{:else if fetchError}
		<p class="error">Failed to load metrics: {fetchError}</p>
	{:else if portrait.rows.length === 0}
		<p class="empty">
			No metrics found for this run set. If these models ran as ensembles, the benchmark may predate
			the fix that makes probabilistic runs emit results.
		</p>
	{:else}
		<div class="stage">
			{#if curveField && curveSeries.length > 0 && leads.length > 0}
				<SkillCurveChart
					title="{selectedRow?.label ?? ''} by lead time"
					{leads}
					series={curveSeries}
					referenceValue={CURVE_REFERENCE[selectedMetric ?? '']?.value ?? 0}
					referenceLabel={CURVE_REFERENCE[selectedMetric ?? '']?.label ?? ''}
					caption={CURVE_REFERENCE[selectedMetric ?? '']?.caption ?? ''}
				/>
			{:else if selectedRow?.group === 'spatial'}
				<p class="stage-note">
					<strong>{selectedRow.label}</strong> varies by grid point — see it on the map. The portrait
					below shows its regional average.
				</p>
			{:else if selectedRow}
				<p class="stage-note">
					<strong>{selectedRow.label}</strong> has no lead-time breakdown. It is scored cumulatively across
					the whole verification window, so only the pooled value below exists.
				</p>
			{/if}
		</div>

		<MetricPortrait
			{portrait}
			{selectedMetric}
			onSelectMetric={(metric) => (selectedMetric = metric)}
		/>
	{/if}
</div>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: 1.5rem;
	}

	.loading,
	.empty {
		color: var(--color-text-dim);
		font-size: 0.85rem;
		margin: 0;
		padding: 1rem 0;
		max-width: 46rem;
		line-height: 1.5;
	}

	.error {
		color: var(--color-danger);
		font-size: 0.85rem;
		margin: 0;
		padding: 1rem 0;
	}

	.stage {
		min-height: 4rem;
	}

	.stage-note {
		margin: 0;
		padding: 1.5rem 0;
		font-size: 0.85rem;
		line-height: 1.5;
		color: var(--color-text-muted);
		max-width: 42rem;
	}

	.stage-note strong {
		color: var(--color-text);
	}
</style>
