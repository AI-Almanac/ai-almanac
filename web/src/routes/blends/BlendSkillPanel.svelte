<script lang="ts">
	/**
	 * The blend's forecast skill: pooled scores per model, and a switchable
	 * lead-time curve.
	 *
	 * Replaces the earlier BlendSkillChart, which plotted only the Area Under ROC
	 * Curve — the one metric on which the blend and climatology are nearly
	 * indistinguishable — and clamped its y axis to [0, 1] so a model worse than
	 * climatology could not be drawn as worse. Both come from
	 * $lib/components/SkillCurveChart now, shared with the benchmarks page.
	 */
	import { BLEND_COLOR, modelColor } from '$lib/chart-colors';
	import SegmentedTabs, { type SegmentedTabOption } from '$lib/components/SegmentedTabs.svelte';
	import SkillCurveChart from '$lib/components/SkillCurveChart.svelte';
	import { skillAgainstReference } from '$lib/metric-portrait';
	import { formatSkillValue, type LeadBin, type SkillCurveSeries } from '$lib/skill-series';
	import BlendSkillMap from './BlendSkillMap.svelte';
	import {
		LEAD_METRICS,
		OVERALL_METRICS,
		SKILL_AXES,
		isDefaultVisibleSeries,
		type LeadMetric,
		type OverallMetric,
		type SkillRow
	} from './blend-summary';

	let { series, jobId }: { series: SkillRow[]; jobId: string } = $props();

	// Diverging fill encoding exactly one quantity: skill relative to Traditional
	// Climatology. Same hues as cellStyle in MetricPortrait.svelte, but a shallower
	// ramp: a raw forecast model here scores around -1 against that baseline, so the
	// portrait's 0.50 ceiling saturates most of this table and the eye lands on the
	// worst row instead of on the blend. Peaks at 0.32 instead.
	const GOOD = '47, 111, 99'; // --color-accent
	const BAD = '166, 84, 60'; // muted rust; earthier than --color-danger on paper
	const TINT_FLOOR = 0.06;
	const TINT_RANGE = 0.26;

	/**
	 * Week bins are ordinal, so `day` is only an x position. That is what lets the
	 * open-ended "Later" bin sit on the axis without pretending to be a lead day.
	 */
	const leads: LeadBin[] = SKILL_AXES.map((label, day) => ({ day, label }));

	const baseline = $derived(series.find((row) => row.isBaseline) ?? null);

	/** Per-lead Brier Skill Score needs the baseline row; without it, it is null. */
	function hasLeadData(metric: LeadMetric): boolean {
		return series.some((row) => row[metric.key].some((value) => value != null));
	}

	const available = $derived(LEAD_METRICS.filter(hasLeadData));
	const options = $derived<SegmentedTabOption[]>(
		available.map((m) => ({ value: m.key, label: m.label }))
	);

	let requested = $state<string | null>(null);
	const metric = $derived(
		available.find((m) => m.key === requested) ?? available[0] ?? LEAD_METRICS[0]
	);

	/**
	 * The blend keeps its own near-black stroke and does not consume a palette
	 * slot, so its constituents keep stable colors as models are added.
	 */
	const colors = $derived.by(() => {
		let next = 0;
		return new Map(
			series.map((row) => [row.model, row.isBlend ? BLEND_COLOR : modelColor(next++)])
		);
	});

	const curves = $derived<SkillCurveSeries[]>(
		series.map((row) => ({
			key: row.model,
			label: row.label,
			color: colors.get(row.model) ?? BLEND_COLOR,
			values: row[metric.key]
		}))
	);

	function rawValue(row: SkillRow, m: OverallMetric): number | null {
		return row[m.key];
	}

	function cellText(row: SkillRow, m: OverallMetric): string {
		const value = rawValue(row, m);
		if (value == null) return '—';
		// A sample count is not a score, so it gets grouping rather than decimals.
		return m.key === 'observations' ? value.toLocaleString() : formatSkillValue(value);
	}

	/** Skill on the shared scale: 0 matches Traditional Climatology, negative is worse. */
	function cellSkill(row: SkillRow, m: OverallMetric): number | null {
		if (m.skillMetric == null) return null;
		const value = rawValue(row, m);
		if (m.skillMetric === 'auc') {
			return skillAgainstReference('auc', value, baseline?.auc ?? null, false);
		}
		return skillAgainstReference(m.skillMetric, value, null, null);
	}

	function cellStyle(row: SkillRow, m: OverallMetric): string {
		const skill = cellSkill(row, m);
		if (skill == null) return '';
		const magnitude = Math.min(Math.abs(skill), 1);
		if (magnitude < 0.02) return '';
		const alpha = TINT_FLOOR + magnitude * TINT_RANGE;
		return `background: rgba(${skill < 0 ? BAD : GOOD}, ${alpha.toFixed(3)})`;
	}

	/**
	 * Every model is normally scored on the same grid-point years, making a whole
	 * column of identical numbers. When that holds it moves to the caption; when a
	 * model was scored on fewer points, that is worth a column.
	 */
	const sharedObservations = $derived.by(() => {
		const counts = series.map((row) => row.observations);
		const first = counts[0];
		return first != null && counts.every((c) => c === first) ? first : null;
	});

	const metrics = $derived(
		sharedObservations == null
			? OVERALL_METRICS
			: OVERALL_METRICS.filter((m) => m.key !== 'observations')
	);
</script>

<section class="skill-panel" aria-label="Forecast skill">
	<div class="overall" data-tour="blend-metrics">
		<div class="overall-scroll">
			<table>
				<caption class="sr-only">
					Pooled forecast skill by model, measured against Traditional Climatology
				</caption>
				<thead>
					<tr>
						<th scope="col" class="model-name">Pooled over all leads</th>
						{#each metrics as m (m.key)}
							<th scope="col" title={m.hint}>{m.label}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each series as row (row.model)}
						<tr class:blend-row={row.isBlend}>
							<th scope="row" class="model-name" class:blend={row.isBlend}>
								<i style={`background: ${colors.get(row.model)}`}></i>
								{row.label}
							</th>
							{#each metrics as m (m.key)}
								<td style={cellStyle(row, m)} class:blend={row.isBlend}>
									{cellText(row, m)}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<p class="caption">
			Shaded by skill against Traditional Climatology: green beats it, rust is worse.
			{#if sharedObservations != null}
				Every model scored on the same {sharedObservations.toLocaleString()} grid-point years.
			{/if}
		</p>
	</div>

	{#if available.length > 0}
		<div class="by-lead">
			<div class="lead-topline">
				<h3>By forecast lead</h3>
				{#if options.length > 1}
					<SegmentedTabs
						{options}
						value={metric.key}
						onSelect={(value) => (requested = value)}
						ariaLabel="Skill metric"
					/>
				{/if}
			</div>
			<SkillCurveChart
				title={metric.label}
				{leads}
				series={curves}
				referenceValue={metric.reference}
				referenceLabel={metric.referenceLabel}
				caption={metric.caption}
				axisPrefix=""
				showTitle={false}
				defaultVisible={isDefaultVisibleSeries}
			/>
		</div>
	{/if}

	<BlendSkillMap {jobId} />

	<details class="glossary">
		<summary>What do “raw”, “calibrated” and “climatology” mean?</summary>
		<dl>
			<div>
				<dt>Raw</dt>
				<dd>
					The forecast model's onset probabilities used straight from the model, with no adjustment.
				</dd>
			</div>
			<div>
				<dt>Calibrated</dt>
				<dd>
					The same probabilities statistically adjusted (Platt scaling) to match observed
					frequencies, so that, e.g., a 30% forecast corresponds to onset actually happening about
					30% of the time. Only the probabilities are adjusted — the model's underlying rainfall
					biases are not corrected.
				</dd>
			</div>
			<div>
				<dt>Traditional Climatology</dt>
				<dd>
					A baseline built only from historical onset frequencies — no forecast model. Knowing least
					of any baseline here, it's the reference every skill score on this page is measured
					against.
				</dd>
			</div>
			<div>
				<dt>Conditional Climatology</dt>
				<dd>
					A stronger version of climatology, which conditions the traditional climatological
					distribution on the fact that onset has not yet occurred by the time of forecast. It was
					introduced in
					<a href="https://arxiv.org/abs/2603.07893" target="_blank" rel="noopener noreferrer"
						>Aitken et al. 2026</a
					>, which argues it is the more appropriate reference for decision-relevant onset
					forecasts.
				</dd>
			</div>
			<div>
				<dt>Blend</dt>
				<dd>The trained combination of the models above — what this job produced.</dd>
			</div>
		</dl>
	</details>
</section>

<style>
	.skill-panel {
		display: flex;
		flex-direction: column;
		gap: 1.1rem;
	}

	.overall {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	/* Models are rows and the four metrics are columns, so width is bounded by
	   the metric count rather than the open-ended model count. Retained only as
	   a safety net for very narrow viewports. */
	.overall-scroll {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.76rem;
	}

	thead th {
		padding: 0.3rem 0.5rem;
		border-bottom: 1px solid var(--color-border);
		color: var(--color-text-muted);
		font-size: 0.68rem;
		font-weight: 700;
		/* Metric names are spelled out in full, so headers wrap instead of
		   forcing the table wider than its container. */
		text-align: right;
		text-wrap: balance;
		vertical-align: bottom;
	}

	/* Legend swatch, tying each row to its curve in the chart below. */
	tbody th i {
		display: inline-block;
		width: 0.5rem;
		height: 0.5rem;
		margin-right: 0.3rem;
		border-radius: 50%;
		vertical-align: baseline;
	}

	/* nowrap keeps every row one line tall; without it the longest label wraps and
	   that row alone grows, which reads as an error rather than a long name. */
	.model-name {
		color: var(--color-text);
		text-align: left;
		white-space: nowrap;
	}

	/* The corner cell labels the row axis; it should not compete with the metric
	   headers beside it. */
	thead .model-name {
		color: var(--color-text-muted);
		font-weight: 600;
	}

	/* The blend is the subject of the page, so its row is marked structurally
	   rather than by color — the fills already encode skill. */
	.blend-row th,
	.blend-row td {
		border-top: 1px solid var(--color-accent-border);
		border-bottom-color: var(--color-accent-border);
	}

	.blend-row .model-name {
		box-shadow: inset 2px 0 0 var(--color-accent);
	}

	tbody td {
		padding: 0.3rem 0.5rem;
		border-bottom: 1px solid var(--color-border-subtle);
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		text-align: right;
		white-space: nowrap;
	}

	tbody th {
		padding: 0.3rem 0.5rem;
		border-bottom: 1px solid var(--color-border-subtle);
		font-weight: 600;
	}

	.blend {
		font-weight: 700;
	}

	.by-lead {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.lead-topline {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
	}

	.lead-topline h3 {
		margin: 0;
		font-size: 0.72rem;
		font-weight: 800;
		color: var(--color-text);
	}

	.caption {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.7rem;
		line-height: 1.45;
	}

	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	.glossary {
		font-size: 0.72rem;
		color: var(--color-text-muted);
	}

	.glossary summary {
		cursor: pointer;
		font-weight: 700;
		color: var(--color-text);
	}

	.glossary dl {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		margin: 0.5rem 0 0;
	}

	.glossary dt {
		font-weight: 700;
		color: var(--color-text);
	}

	.glossary dd {
		margin: 0;
		line-height: 1.45;
	}

	/* Citations sit inside muted body text; the default link blue fights it. */
	.glossary a {
		color: var(--color-accent);
	}
</style>
