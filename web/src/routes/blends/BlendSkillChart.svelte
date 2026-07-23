<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';
	import { SKILL_AXES, type SkillRow } from './blend-summary';

	let { series }: { series: SkillRow[] } = $props();

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

	const BLEND_COLOR = '#1f2937';
	const MODEL_COLORS = [
		'#0f766e',
		'#b2182b',
		'#d06f1a',
		'#6b5b95',
		'#2d7d46',
		'#9333ea',
		'#0891b2'
	];
	const X = SKILL_AXES.map((_, i) => i);

	type SeriesDef = {
		key: string;
		label: string;
		color: string;
		width: number;
		isBlend: boolean;
		values: number[];
	};

	const seriesDefs = $derived(buildSeriesDefs(series));
	const hasVisibleSeries = $derived(seriesDefs.some((s) => visibleSeries[s.key]));

	function buildSeriesDefs(rows: SkillRow[]): SeriesDef[] {
		let next = 0;
		return rows.map((row) => ({
			key: row.model,
			label: row.label,
			color: row.isBlend ? BLEND_COLOR : MODEL_COLORS[next++ % MODEL_COLORS.length],
			width: row.isBlend ? 3 : 1.75,
			isBlend: row.isBlend,
			values: row.aucByLead
		}));
	}

	function chartData(): uPlot.AlignedData {
		return [X, ...seriesDefs.map((s) => s.values)];
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
		for (let s = 1; s < plot.data.length; s++) {
			if (!plot.series[s]?.show) continue;
			const value = plot.data[s][idx];
			if (value == null) continue;
			rows.push({ label: seriesDefs[s - 1].label, color: seriesDefs[s - 1].color, value });
			anchorY = Math.min(anchorY, plot.valToPos(value, 'y'));
		}
		if (rows.length === 0) {
			hidePointTooltip();
			return;
		}
		pointTooltip = {
			axis: SKILL_AXES[idx],
			rows: rows.sort((a, b) => b.value - a.value),
			x: plotRect.left + plot.valToPos(idx, 'x'),
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
				x: { time: false, range: () => [-0.25, X.length - 1 + 0.25] },
				y: {
					range: (_plot, min, max) => {
						if (min === null || max === null) return [0.5, 1];
						const pad = Math.max((max - min) * 0.15, 0.02);
						return [Math.max(0, min - pad), Math.min(1, max + pad)];
					}
				}
			},
			axes: [
				{
					stroke: '#6a7779',
					grid: { show: false },
					ticks: { show: false },
					size: 28,
					splits: () => X,
					values: (_plot, vals) => vals.map((v) => SKILL_AXES[v] ?? ''),
					gap: 8
				},
				{
					stroke: '#6a7779',
					grid: { stroke: 'rgba(31, 43, 52, 0.1)', width: 1 },
					ticks: { show: false },
					size: 40,
					values: (_plot, vals) => vals.map((v) => v.toFixed(2)),
					gap: 8
				}
			],
			series: [
				{},
				...seriesDefs.map((s) => ({
					label: s.label,
					stroke: s.color,
					width: s.width,
					show: visibleSeries[s.key] ?? true,
					spanGaps: true,
					points: {
						show: true,
						size: s.isBlend ? 8 : 6,
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
		const bounds = chartHost.getBoundingClientRect();
		chart?.destroy();
		chart = new uPlot(
			makeOptions(Math.max(1, Math.floor(bounds.width)), Math.max(1, Math.floor(bounds.height))),
			chartData(),
			chartHost
		);
		resizeChart();
	}

	onMount(() => {
		resizeObserver = new ResizeObserver(resizeChart);
		if (chartHost) resizeObserver.observe(chartHost);
	});

	// Default new series to visible, drop toggles for series that disappeared.
	$effect(() => {
		const next = { ...visibleSeries };
		let changed = false;
		for (const s of seriesDefs) {
			if (next[s.key] == null) {
				next[s.key] = true;
				changed = true;
			}
		}
		for (const key of Object.keys(next)) {
			if (!seriesDefs.some((s) => s.key === key)) {
				delete next[key];
				changed = true;
			}
		}
		if (changed) visibleSeries = next;
	});

	$effect(() => {
		if (!hasVisibleSeries || !chartHost) {
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

<section class="skill-chart" aria-label="Forecast skill by lead time">
	<div class="chart-topline">
		<span>AUC by lead time</span>
		<div class="series-toggles" aria-label="Toggle models">
			{#each seriesDefs as s (s.key)}
				<button
					type="button"
					class:muted={!visibleSeries[s.key]}
					class:blend={s.isBlend}
					onclick={() => toggleSeries(s.key)}
					aria-pressed={visibleSeries[s.key] ?? true}
				>
					<i style={`background: ${s.color}`}></i>
					{s.label}
				</button>
			{/each}
		</div>
	</div>

	{#if hasVisibleSeries}
		<div class="chart-host" bind:this={chartHost} aria-label="AUC by forecast lead time"></div>
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
							<span>{row.value.toFixed(3)}</span>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	{:else}
		<p class="empty-series">Select at least one model to show the plot.</p>
	{/if}

	<p class="caption">
		Discrimination skill (AUC) by forecast lead time. 0.5 is no skill, 1.0 is perfect — higher is
		better. Click a model to toggle it; hover for exact values.
	</p>

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
					The same probabilities statistically adjusted (Platt scaling) so that, e.g., a 30%
					forecast corresponds to onset actually happening about 30% of the time.
				</dd>
			</div>
			<div>
				<dt>Climatology</dt>
				<dd>
					A baseline built only from historical onset frequency — no forecast model. A useful
					forecast has to beat it.
				</dd>
			</div>
			<div>
				<dt>Climatology (uncalibrated)</dt>
				<dd>
					The climatology baseline before that adjustment; it's the reference the skill scores are
					measured against.
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
	.skill-chart {
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

	.series-toggles button.blend {
		color: var(--color-text);
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
		height: clamp(12rem, 30vh, 18rem);
		min-height: 12rem;
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

	.glossary {
		font-size: 0.8rem;
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
		line-height: 1.4;
	}
</style>
