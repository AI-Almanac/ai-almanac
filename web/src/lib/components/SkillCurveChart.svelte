<script lang="ts">
	/**
	 * A probabilistic skill score plotted against forecast lead time.
	 *
	 * Structurally a sibling of routes/blends/BlendSkillChart.svelte, with three
	 * differences that matter:
	 *
	 * 1. The y range is not clamped to [0, 1]. The Brier Skill Score is unbounded
	 *    below — a model worse than climatology scores negative, and hiding that
	 *    would be the single most misleading thing this chart could do.
	 * 2. It draws a reference line at the no-skill value (zero for skill scores,
	 *    0.5 for the Area Under ROC Curve). Without it the curves are
	 *    uninterpretable.
	 * 3. The x axis is real lead days, not categorical week bins, so bins from
	 *    both verification windows compose onto one 1-30 axis.
	 */
	import { onDestroy } from 'svelte';
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';
	import { AXIS_STROKE, GRID_STROKE, REFERENCE_STROKE } from '$lib/chart-colors';
	import type { LeadBin, SkillCurveSeries } from '$lib/skill-series';
	import { formatSkillValue } from '$lib/skill-series';

	type Props = {
		title: string;
		leads: LeadBin[];
		series: SkillCurveSeries[];
		/** Value at which the score indicates no skill. */
		referenceValue: number;
		referenceLabel: string;
		caption: string;
	};

	let { title, leads, series, referenceValue, referenceLabel, caption }: Props = $props();

	let chartHost = $state<HTMLDivElement | null>(null);
	let chart: uPlot | null = null;
	let resizeObserver: ResizeObserver | null = null;
	let visibleSeries = $state<Record<string, boolean>>({});
	let pointTooltip = $state<{
		axis: string;
		rows: { label: string; color: string; value: number }[];
		x: number;
		y: number;
	} | null>(null);

	const xValues = $derived(leads.map((l) => l.day));
	const hasVisibleSeries = $derived(series.some((s) => visibleSeries[s.key]));

	function chartData(): uPlot.AlignedData {
		return [
			xValues,
			// The reference line is a real series so it participates in the y-range
			// calculation and can never fall outside the visible plot.
			xValues.map(() => referenceValue),
			...series.map((s) => s.values)
		] as uPlot.AlignedData;
	}

	function hidePointTooltip() {
		pointTooltip = null;
	}

	function updatePointTooltip(plot: uPlot) {
		const idx = plot.cursor.idx;
		if (idx == null || plot.cursor.left == null) {
			hidePointTooltip();
			return;
		}
		const plotRect = plot.over.getBoundingClientRect();
		const rows: { label: string; color: string; value: number }[] = [];
		let anchorY = Number.POSITIVE_INFINITY;
		// Series index 1 is the reference line; model series start at 2.
		for (let s = 2; s < plot.data.length; s++) {
			if (!plot.series[s]?.show) continue;
			const value = plot.data[s][idx];
			if (value == null) continue;
			// Optional-chained because setCursor can fire from a pending mouse event
			// after the series prop shrank but before the rebuild effect flushed.
			const def = series[s - 2];
			if (!def) continue;
			rows.push({ label: def.label, color: def.color, value });
			anchorY = Math.min(anchorY, plot.valToPos(value, 'y'));
		}
		if (rows.length === 0) {
			hidePointTooltip();
			return;
		}
		pointTooltip = {
			axis: `Days ${leads[idx]?.label ?? ''}`,
			// Higher is better for every score this chart draws, so best-first.
			rows: rows.sort((a, b) => b.value - a.value),
			x: plotRect.left + plot.valToPos(xValues[idx], 'x'),
			y: plotRect.top + anchorY
		};
	}

	function makeOptions(width: number, height: number): uPlot.Options {
		return {
			width,
			height,
			padding: [8, 8, 0, 0],
			legend: { show: false },
			cursor: { drag: { x: false, y: false } },
			hooks: {
				setCursor: [updatePointTooltip],
				ready: [(plot) => plot.over.addEventListener('mouseleave', hidePointTooltip)],
				destroy: [(plot) => plot.over.removeEventListener('mouseleave', hidePointTooltip)]
			} as Partial<uPlot.Hooks.Arrays> as uPlot.Hooks.Arrays,
			scales: {
				x: {
					time: false,
					range: () => {
						const first = xValues[0] ?? 0;
						const last = xValues[xValues.length - 1] ?? 1;
						const pad = Math.max((last - first) * 0.06, 1);
						return [first - pad, last + pad];
					}
				},
				y: {
					// Deliberately unbounded: a skill score below zero means worse than
					// climatology and must stay visible. The reference value is always
					// included so the no-skill line never sits off-plot.
					range: (_plot, min, max) => {
						if (min === null || max === null) return [referenceValue - 0.5, referenceValue + 0.5];
						const lo = Math.min(min, referenceValue);
						const hi = Math.max(max, referenceValue);
						const pad = Math.max((hi - lo) * 0.15, 0.02);
						return [lo - pad, hi + pad];
					}
				}
			},
			axes: [
				{
					stroke: AXIS_STROKE,
					grid: { show: false },
					ticks: { show: false },
					size: 30,
					splits: () => xValues,
					values: (_plot, vals) => vals.map((v) => leads.find((l) => l.day === v)?.label ?? ''),
					gap: 8
				},
				{
					stroke: AXIS_STROKE,
					grid: { stroke: GRID_STROKE, width: 1 },
					ticks: { show: false },
					size: 44,
					values: (_plot, vals) => vals.map((v) => v.toFixed(2)),
					gap: 8
				}
			],
			series: [
				{},
				{
					label: referenceLabel,
					stroke: REFERENCE_STROKE,
					width: 1,
					dash: [4, 4],
					points: { show: false }
				},
				...series.map((s) => ({
					label: s.label,
					stroke: s.color,
					width: 1.75,
					show: visibleSeries[s.key] ?? true,
					spanGaps: true,
					points: {
						show: true,
						size: 6,
						width: 1.5,
						stroke: s.color,
						fill: '#ffffff'
					}
				}))
			]
		};
	}

	function resizeChart() {
		if (!chartHost || !chart) return;
		const bounds = chartHost.getBoundingClientRect();
		chart.setSize({
			width: Math.max(1, Math.floor(bounds.width)),
			height: Math.max(1, Math.floor(bounds.height))
		});
	}

	function buildChart() {
		if (!chartHost) return;
		// The tooltip is position: fixed and outlives the chart it describes. Without
		// this, arriving data while the cursor is inside the plot leaves stale rows
		// pinned at stale coordinates until the next mousemove.
		hidePointTooltip();
		const bounds = chartHost.getBoundingClientRect();
		chart?.destroy();
		chart = new uPlot(
			makeOptions(Math.max(1, Math.floor(bounds.width)), Math.max(1, Math.floor(bounds.height))),
			chartData(),
			chartHost
		);
		// Observe here rather than in onMount: the host is behind an {#if}, so on
		// first mount it does not exist yet and an onMount observe() would be
		// silently skipped and never retried.
		resizeObserver ??= new ResizeObserver(resizeChart);
		resizeObserver.disconnect();
		resizeObserver.observe(chartHost);
		resizeChart();
	}

	// Default new series to visible, drop toggles for series that disappeared.
	$effect(() => {
		const next = { ...visibleSeries };
		let changed = false;
		for (const s of series) {
			if (next[s.key] == null) {
				next[s.key] = true;
				changed = true;
			}
		}
		for (const key of Object.keys(next)) {
			if (!series.some((s) => s.key === key)) {
				delete next[key];
				changed = true;
			}
		}
		if (changed) visibleSeries = next;
	});

	$effect(() => {
		if (!hasVisibleSeries || !chartHost || leads.length === 0) {
			chart?.destroy();
			chart = null;
			return;
		}
		buildChart();
	});

	function toggleSeries(key: string) {
		hidePointTooltip();
		visibleSeries = { ...visibleSeries, [key]: !(visibleSeries[key] ?? true) };
	}

	onDestroy(() => {
		resizeObserver?.disconnect();
		chart?.destroy();
		chart = null;
	});
</script>

<section class="skill-curve" aria-label={title}>
	<div class="chart-topline">
		<span>{title}</span>
		<div class="series-toggles" aria-label="Toggle models">
			{#each series as s (s.key)}
				<button
					type="button"
					class:muted={!visibleSeries[s.key]}
					onclick={() => toggleSeries(s.key)}
					aria-pressed={visibleSeries[s.key] ?? true}
				>
					<i style={`background: ${s.color}`}></i>
					{s.label}
				</button>
			{/each}
		</div>
	</div>

	{#if leads.length === 0}
		<p class="empty-series">No lead-time bins in this result.</p>
	{:else if hasVisibleSeries}
		<div class="chart-host" bind:this={chartHost} aria-label={title}></div>
		{#if pointTooltip}
			<div
				class="chart-point-tooltip"
				style={`left: ${pointTooltip.x}px; top: ${pointTooltip.y}px`}
				role="tooltip"
			>
				<strong>{pointTooltip.axis}</strong>
				<div class="tooltip-rows">
					{#each pointTooltip.rows as row (row.label)}
						<div class="tooltip-row">
							<span class="tooltip-series">
								<i style={`background: ${row.color}`}></i>
								{row.label}
							</span>
							<span>{formatSkillValue(row.value)}</span>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	{:else}
		<p class="empty-series">Select at least one model to show the plot.</p>
	{/if}

	<p class="caption">{caption}</p>
</section>

<style>
	.skill-curve {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.chart-topline {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
		font-size: 0.72rem;
		font-weight: 800;
		color: var(--color-text);
	}

	.series-toggles {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: 0.35rem;
	}

	.series-toggles button {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		min-height: 1.45rem;
		padding: 0.18rem 0.48rem;
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.66rem;
		font-weight: 700;
		cursor: pointer;
	}

	.series-toggles button.muted {
		opacity: 0.45;
		background: var(--color-surface-muted);
	}

	.series-toggles i {
		width: 0.7rem;
		height: 0.18rem;
		border-radius: 999rem;
	}

	.chart-host {
		width: 100%;
		height: clamp(11rem, 26vh, 16rem);
		min-height: 11rem;
	}

	.chart-host :global(.uplot) {
		width: 100%;
		height: 100%;
		background: transparent;
		font-family: var(--font-mono);
		color: var(--color-text-muted);
	}

	.chart-point-tooltip {
		position: fixed;
		z-index: 1000;
		transform: translate(0.65rem, calc(-100% - 0.65rem));
		max-width: min(16rem, calc(100vw - 1.5rem));
		padding: 0.5rem 0.6rem;
		border: 1px solid rgba(218, 232, 226, 0.95);
		border-radius: 0.5rem;
		background: rgba(19, 31, 36, 0.96);
		box-shadow: 0 0.75rem 2.25rem rgba(3, 14, 25, 0.32);
		color: #f7fbfa;
		font-size: 0.72rem;
		line-height: 1.35;
		pointer-events: none;
	}

	.chart-point-tooltip strong {
		display: block;
		margin-bottom: 0.35rem;
		font-family: var(--font-mono);
		font-size: 0.78rem;
	}

	.tooltip-rows {
		display: grid;
		gap: 0.28rem;
	}

	.tooltip-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 0.9rem;
	}

	.tooltip-series {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		color: #d7e3df;
		font-weight: 700;
	}

	.tooltip-series i {
		width: 0.8rem;
		height: 0.18rem;
		border-radius: 999rem;
	}

	.tooltip-row > span:last-child {
		color: #ffffff;
		font-family: var(--font-mono);
	}

	.empty-series {
		margin: 0;
		padding: 2rem 0.75rem;
		font-size: 0.78rem;
		color: var(--color-text-muted);
		text-align: center;
	}

	.caption {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.8rem;
		line-height: 1.4;
	}
</style>
