<script lang="ts">
	import type { JobCellResponse, MetricDefinition } from '$lib/api';
	import YearOnsetHeatmap from '$lib/components/YearOnsetHeatmap.svelte';
	import {
		formatMetricDelta,
		formatMetricValue,
		isAnnualMaeMetric,
		metricLabel as displayMetricLabel,
		metricMap,
		metricUnit,
		orderMetricKeys,
		windowLabel
	} from '$lib/metric-metadata';

	type MetricDef = { value: string; label: string };

	type Props = {
		result: JobCellResponse;
		metrics: MetricDef[];
	};

	let { result, metrics }: Props = $props();
	const definitionsById = $derived(
		metricMap(
			metrics.map(
				(metric) =>
					({
						id: metric.value,
						label: metric.label,
						abbreviation: metric.label,
						unit: null,
						description: ''
					}) satisfies MetricDefinition
			)
		)
	);
	const visibleMetrics = $derived(
		orderMetricKeys(
			Object.keys(result.metrics).filter((metric) => !isAnnualMaeMetric(metric)),
			definitionsById
		)
	);

	function metricLabel(metricValue: string) {
		return displayMetricLabel(metricValue, definitionsById);
	}

	function modelDisplayName(modelName: string) {
		const labels: Record<string, string> = {
			fuxi: 'FuXi',
			aifs: 'AIFS',
			aifs_daily: 'AIFS Daily',
			fuxi_s2s: 'FuXi S2S',
			climatology: 'Climatology'
		};
		return labels[modelName.toLowerCase()] ?? modelName;
	}

	function tileUnit(metricValue: string, apiUnit: string | undefined): string {
		return metricUnit(metricValue, apiUnit, definitionsById);
	}
</script>

<article class="cell-model-card">
	<div class="cell-model-heading">
		<div>
			<p class="cell-model-label">{modelDisplayName(result.model)}</p>
			<p class="cell-grid-note">
				Nearest grid: latitude {result.lat.toFixed(2)}°N, longitude {result.lon.toFixed(2)}°E
			</p>
		</div>
		<span class="cell-window">{windowLabel(result.window)}</span>
	</div>

	<div class="metric-tiles">
		{#each visibleMetrics as metricValue}
			{@const item = result.metrics[metricValue]}
			{@const unit = tileUnit(metricValue, item?.unit)}
			<div class="metric-tile">
				<span class="metric-name">{metricLabel(metricValue)}</span>
				<strong>{formatMetricValue(item?.model, unit)}</strong>
				<span class="metric-context">
					Δ {formatMetricDelta(item?.delta, unit)}
				</span>
			</div>
		{/each}
	</div>

	{#if result.mae_series.length > 0}
		<YearOnsetHeatmap model={result.model} series={result.mae_series} />
	{:else}
		<div class="onset-years empty">No annual onset records were found for this cell.</div>
	{/if}
</article>

<style>
	.cell-model-card {
		flex: 0 0 auto;
		border: 0.0625rem solid rgba(31, 43, 52, 0.1);
		border-radius: 0.8rem;
		background: rgba(255, 255, 255, 0.9);
		box-shadow: 0 0.5rem 1.625rem rgba(20, 40, 50, 0.09);
		overflow: hidden;
	}

	.cell-model-heading {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.7rem 0.8rem;
		border-bottom: 0.0625rem solid rgba(31, 43, 52, 0.1);
		background: linear-gradient(90deg, rgba(248, 251, 250, 0.98), rgba(233, 242, 238, 0.78));
	}

	.cell-model-label {
		margin: 0;
		font-size: 0.82rem;
		font-weight: 800;
		letter-spacing: 0.06em;
		color: #17252b;
	}

	.cell-grid-note {
		margin: 0.2rem 0 0;
		font-size: 0.66rem;
		color: #6c7778;
	}

	.cell-window {
		flex-shrink: 0;
		padding: 0.14rem 0.45rem;
		border-radius: 999rem;
		background: #edf4f1;
		color: #45615f;
		font-size: 0.61rem;
		font-weight: 700;
	}

	.metric-tiles {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 0.55rem;
		padding: 0.65rem 0.75rem;
	}

	.metric-tile {
		padding: 0.48rem;
		border-radius: 0.55rem;
		background: linear-gradient(180deg, #fbfdfc, #f3f7f5);
		border: 0.0625rem solid rgba(31, 43, 52, 0.08);
	}

	.metric-name,
	.metric-context {
		display: block;
		font-size: 0.62rem;
		color: #687679;
	}

	.metric-tile strong {
		display: block;
		margin: 0.12rem 0;
		font-family: var(--font-mono);
		font-size: 0.95rem;
		color: #16252a;
	}

	.onset-years.empty {
		margin: 0 0.75rem 0.75rem;
		padding: 0.55rem 0.6rem 0.6rem;
		border: 0.0625rem solid rgba(31, 43, 52, 0.08);
		border-radius: 0.6rem;
		background: linear-gradient(180deg, #fbfdfc, #f3f7f5);
		color: #687679;
		font-size: 0.72rem;
	}

	@media (max-width: 45rem) {
		.metric-tiles {
			grid-template-columns: 1fr;
		}
	}
</style>
