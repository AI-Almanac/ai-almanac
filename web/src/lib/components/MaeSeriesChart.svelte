<script lang="ts">
	import { onDestroy } from 'svelte';
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';
	import type { JobCellResponse } from '$lib/api';
	import { BASELINE_COLOR, MODEL_COLORS } from '$lib/chart-colors';

	type Props = {
		results: JobCellResponse[];
	};

	let { results }: Props = $props();

	let chartHost = $state<HTMLDivElement | null>(null);
	let chart: uPlot | null = null;
	let resizeObserver: ResizeObserver | null = null;
	let visibleSeries = $state<Record<string, boolean>>({});
	let pointTooltip = $state<{
		year: number;
		rows: { label: string; color: string; value: number }[];
		x: number;
		y: number;
	} | null>(null);

	type SeriesDef = {
		key: string;
		label: string;
		color: string;
		dash?: number[];
		values: Map<number, number | null>;
	};

	const modelResults = $derived(results.filter((result) => result.mae_series.length > 0));
	const seriesDefs = $derived(buildSeriesDefs(modelResults));
	const years = $derived(
		[
			...new Set(modelResults.flatMap((result) => result.mae_series.map((point) => point.year)))
		].sort((a, b) => a - b)
	);
	const hasVisibleSeries = $derived(seriesDefs.some((series) => visibleSeries[series.key]));

	function buildSeriesDefs(source: JobCellResponse[]): SeriesDef[] {
		const baselineValues = new Map<number, number | null>();
		for (const result of source) {
			for (const point of result.mae_series) {
				if (!baselineValues.has(point.year) && point.baseline != null) {
					baselineValues.set(point.year, point.baseline);
				}
			}
		}

		return [
			{
				key: 'climatology',
				label: 'climatology',
				color: BASELINE_COLOR,
				dash: [8, 6],
				values: baselineValues
			},
			...source.map((result, index) => ({
				key: `model:${result.job_id}`,
				label: result.model,
				color: MODEL_COLORS[index % MODEL_COLORS.length],
				values: new Map(result.mae_series.map((point) => [point.year, point.model]))
			}))
		];
	}

	function chartData(): uPlot.AlignedData {
		return [
			years,
			...seriesDefs.map((series) => years.map((year) => series.values.get(year) ?? null))
		];
	}

	function hidePointTooltip() {
		pointTooltip = null;
	}

	function updatePointTooltip(plot: uPlot) {
		const idx = plot.cursor.idx;
		if (idx == null || plot.cursor.left == null || plot.cursor.top == null) {
			hidePointTooltip();
			return;
		}

		const year = years[idx];
		const plotRect = plot.over.getBoundingClientRect();
		const rows: { label: string; color: string; value: number; y: number }[] = [];
		let anchorY = Number.POSITIVE_INFINITY;

		for (let seriesIndex = 1; seriesIndex < plot.data.length; seriesIndex++) {
			if (!plot.series[seriesIndex]?.show) continue;
			const value = plot.data[seriesIndex][idx];
			if (value == null) continue;

			const y = plot.valToPos(value, 'y');
			const series = seriesDefs[seriesIndex - 1];
			rows.push({ label: series.label, color: series.color, value, y });
			anchorY = Math.min(anchorY, y);
		}

		if (rows.length === 0) {
			hidePointTooltip();
			return;
		}

		const x = plot.valToPos(year, 'x');
		pointTooltip = {
			year,
			rows: rows.sort((a, b) => a.value - b.value),
			x: plotRect.left + x,
			y: plotRect.top + anchorY
		};
	}

	function makeOptions(width: number, height: number): uPlot.Options {
		return {
			width,
			height,
			padding: [0, 0, 0, 0],
			legend: {
				show: false
			},
			cursor: {
				drag: {
					x: false,
					y: false
				}
			},
			hooks: {
				setCursor: [updatePointTooltip],
				ready: [
					(plot) => {
						plot.over.addEventListener('mouseleave', hidePointTooltip);
					}
				],
				destroy: [
					(plot) => {
						plot.over.removeEventListener('mouseleave', hidePointTooltip);
					}
				]
			} as Partial<uPlot.Hooks.Arrays> as uPlot.Hooks.Arrays,
			scales: {
				x: {
					time: false
				},
				y: {
					range: (_plot, min, max) => {
						if (min === null || max === null) return [0, 1];
						if (min === max) return [Math.max(0, min - 1), max + 1];
						const pad = (max - min) * 0.15;
						const lower = min <= pad ? min - pad : Math.max(0, min - pad);
						return [lower, max + pad];
					}
				}
			},
			axes: [
				{
					stroke: '#6a7779',
					grid: {
						show: false
					},
					ticks: {
						show: false
					},
					size: 28,
					values: (_plot, values) => values.map((value) => String(value)),
					gap: 8
				},
				{
					stroke: '#6a7779',
					grid: {
						stroke: 'rgba(31, 43, 52, 0.1)',
						width: 1
					},
					ticks: {
						show: false
					},
					size: 44,
					values: (_plot, values) => values.map((value) => `${value.toFixed(1)} d`),
					gap: 8
				}
			],
			series: [
				{},
				...seriesDefs.map((series) => ({
					label: series.label,
					stroke: series.color,
					width: 2.5,
					dash: series.dash,
					show: visibleSeries[series.key] ?? true,
					spanGaps: true,
					points: {
						show: true,
						size: 6,
						width: 1.5,
						stroke: series.color,
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

	$effect(() => {
		const next = { ...visibleSeries };
		let changed = false;
		for (const series of seriesDefs) {
			if (next[series.key] == null) {
				next[series.key] = true;
				changed = true;
			}
		}
		for (const key of Object.keys(next)) {
			if (!seriesDefs.some((series) => series.key === key)) {
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

		hidePointTooltip();
		const bounds = chartHost.getBoundingClientRect();
		chart?.destroy();
		chart = new uPlot(
			makeOptions(Math.max(1, Math.floor(bounds.width)), Math.max(1, Math.floor(bounds.height))),
			chartData(),
			chartHost
		);
		// Observe here rather than in onMount: chartHost is bound inside
		// {#if hasVisibleSeries}, which is false on first render because
		// visibleSeries starts empty. An onMount observe() therefore always
		// early-returned, and this chart never responded to container resizes.
		resizeObserver ??= new ResizeObserver(resizeChart);
		resizeObserver.disconnect();
		resizeObserver.observe(chartHost);
		resizeChart();
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

<section class="mae-chart-panel" aria-label="Annual MAE comparison">
	<div class="chart-topline">
		<span>Annual MAE</span>
		<div class="series-toggles" aria-label="Toggle chart series">
			{#each seriesDefs as series}
				<button
					type="button"
					class:muted={!visibleSeries[series.key]}
					onclick={() => toggleSeries(series.key)}
					aria-pressed={visibleSeries[series.key] ?? true}
				>
					<i style={`background: ${series.color}`}></i>
					{series.label}
				</button>
			{/each}
		</div>
	</div>

	{#if hasVisibleSeries}
		<div class="mae-chart-host" bind:this={chartHost} aria-label="Annual MAE by year"></div>
		<p class="missing-note">
			<span></span>
			Years without a detected onset are left blank.
		</p>
		{#if pointTooltip}
			<div
				class="chart-point-tooltip"
				style={`left: ${pointTooltip.x}px; top: ${pointTooltip.y}px`}
				role="tooltip"
			>
				<strong>{pointTooltip.year}</strong>
				<div class="tooltip-rows">
					{#each pointTooltip.rows as row}
						<div class="tooltip-row">
							<span class="tooltip-series">
								<i style={`background: ${row.color}`}></i>
								{row.label}
							</span>
							<span>{row.value.toFixed(2)} d</span>
						</div>
					{/each}
				</div>
			</div>
		{/if}
	{:else}
		<p class="empty-series">Select at least one series to show the annual MAE plot.</p>
	{/if}
</section>

<style>
	.mae-chart-panel {
		flex: 0 0 auto;
		padding: 0.65rem 0.75rem 0.7rem;
		border: 0.0625rem solid rgba(31, 43, 52, 0.1);
		border-radius: 0.8rem;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.95), rgba(242, 247, 245, 0.95));
		box-shadow: 0 0.5rem 1.625rem rgba(20, 40, 50, 0.08);
	}

	.chart-topline {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 0.35rem;
		font-size: 0.72rem;
		font-weight: 800;
		color: #25363c;
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
		border: 0.0625rem solid rgba(31, 43, 52, 0.12);
		border-radius: 999rem;
		background: #ffffff;
		color: #44595b;
		font: inherit;
		font-size: 0.62rem;
		font-weight: 700;
		cursor: pointer;
	}

	.series-toggles button.muted {
		opacity: 0.45;
		background: #eef3f1;
	}

	.series-toggles i {
		width: 0.7rem;
		height: 0.16rem;
		border-radius: 999rem;
	}

	.mae-chart-host {
		width: 100%;
		height: clamp(11rem, 28vh, 17rem);
		min-height: 11rem;
	}

	.mae-chart-host :global(.uplot) {
		width: 100%;
		height: 100%;
		background: transparent;
		font-family: var(--font-mono);
		color: #6a7779;
	}

	.mae-chart-host :global(.u-over),
	.mae-chart-host :global(.u-under),
	.mae-chart-host :global(canvas) {
		border-radius: 0.4rem;
	}

	.mae-chart-host :global(.u-cursor-x),
	.mae-chart-host :global(.u-cursor-y) {
		background: rgba(31, 43, 52, 0.18);
	}

	.missing-note {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		margin: 0.15rem 0 0;
		color: #687679;
		font-size: 0.66rem;
		line-height: 1.35;
	}

	.missing-note span {
		width: 0.75rem;
		height: 0.125rem;
		border-radius: 999rem;
		background: repeating-linear-gradient(
			90deg,
			rgba(104, 118, 121, 0.7),
			rgba(104, 118, 121, 0.7) 0.18rem,
			transparent 0.18rem,
			transparent 0.32rem
		);
	}

	.chart-point-tooltip {
		position: fixed;
		z-index: 1000;
		transform: translate(0.65rem, calc(-100% - 0.65rem));
		max-width: min(16rem, calc(100vw - 1.5rem));
		padding: 0.5rem 0.6rem;
		border: 0.0625rem solid rgba(218, 232, 226, 0.95);
		border-radius: 0.5rem;
		background: rgba(19, 31, 36, 0.96);
		box-shadow: 0 0.75rem 2.25rem rgba(3, 14, 25, 0.32);
		color: #f7fbfa;
		font-size: 0.72rem;
		line-height: 1.35;
		pointer-events: none;
	}

	.chart-point-tooltip strong,
	.chart-point-tooltip span {
		display: block;
	}

	.chart-point-tooltip strong {
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
		display: flex !important;
		align-items: center;
		gap: 0.35rem;
		color: #d7e3df;
		font-weight: 700;
	}

	.tooltip-series i {
		width: 0.8rem;
		height: 0.16rem;
		border-radius: 999rem;
	}

	.tooltip-row > span:last-child {
		color: #ffffff;
		font-family: var(--font-mono);
	}

	.empty-series {
		margin: 0;
		padding: 2rem 0.75rem;
		font-size: 0.75rem;
		color: #687679;
		text-align: center;
	}

	@media (max-width: 45rem) {
		.chart-topline {
			flex-direction: column;
		}

		.series-toggles {
			justify-content: flex-start;
		}
	}
</style>
