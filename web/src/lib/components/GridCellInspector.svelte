<script lang="ts">
	import type { JobCellResponse } from '$lib/api';
	import GridCellModelCard from '$lib/components/GridCellModelCard.svelte';
	import MaeSeriesChart from '$lib/components/MaeSeriesChart.svelte';
	import { windowLabel } from '$lib/metric-metadata';
	import { formatCoord } from '$lib/geo';

	type MetricDef = { value: string; label: string };

	type Props = {
		cell: { lat: number; lon: number };
		forecastWindow: string;
		metrics: MetricDef[];
		results: JobCellResponse[];
		loading: boolean;
		error: string | null;
		onclose: () => void;
	};

	let { cell, forecastWindow, metrics, results, loading, error, onclose }: Props = $props();
</script>

<div class="cell-backdrop" role="presentation" onclick={onclose}></div>
<div
	class="cell-inspector"
	role="dialog"
	aria-modal="true"
	aria-label="Grid cell metrics"
	onwheel={(e) => e.stopPropagation()}
>
	<header class="cell-header">
		<div>
			<p class="cell-kicker">Grid Cell Inspector</p>
			<h3>
				Latitude {formatCoord(cell.lat, 'N', 'S')}, longitude {formatCoord(cell.lon, 'E', 'W')}
			</h3>
			<p class="cell-subtitle">
				{windowLabel(forecastWindow)}, compared with climatology when available
			</p>
		</div>
		<button class="cell-close" onclick={onclose} aria-label="Close cell inspector">×</button>
	</header>

	{#if loading}
		<div class="cell-state">Loading cell metrics…</div>
	{:else if error}
		<div class="cell-state error">{error}</div>
	{:else if results.length === 0}
		<div class="cell-state">No cell metrics available.</div>
	{:else}
		<div class="cell-body">
			{#if results.some((result) => result.mae_series.length > 0)}
				<MaeSeriesChart {results} />
			{/if}

			{#each results as result}
				<GridCellModelCard {result} {metrics} />
			{/each}
		</div>
	{/if}
</div>

<style>
	.cell-backdrop {
		position: absolute;
		inset: 0;
		z-index: 35;
		background:
			radial-gradient(circle at 22% 18%, rgba(238, 247, 242, 0.22), transparent 18rem),
			linear-gradient(90deg, rgba(9, 22, 35, 0.56), rgba(9, 22, 35, 0.22));
		backdrop-filter: blur(0.1875rem) saturate(0.8);
	}

	.cell-inspector {
		position: absolute;
		top: 1rem;
		left: 1rem;
		bottom: 1rem;
		z-index: 40;
		width: min(64vw, calc(100% - 2rem));
		min-height: 0;
		display: flex;
		flex-direction: column;
		overflow: hidden;
		border: 0.0625rem solid rgba(218, 232, 226, 0.9);
		border-radius: 1rem;
		background:
			linear-gradient(145deg, rgba(255, 255, 255, 0.97), rgba(239, 247, 243, 0.96)),
			var(--color-surface);
		box-shadow: 0 1.5rem 4.375rem rgba(3, 14, 25, 0.36);
		color: #1f2b34;
	}

	.cell-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.85rem 1rem 0.75rem;
		border-bottom: 0.0625rem solid rgba(31, 43, 52, 0.12);
		background:
			linear-gradient(135deg, rgba(216, 233, 224, 0.95), rgba(255, 255, 255, 0.62)),
			repeating-linear-gradient(
				-45deg,
				rgba(42, 76, 82, 0.04),
				rgba(42, 76, 82, 0.04) 0.0625rem,
				transparent 0.0625rem,
				transparent 0.5rem
			);
	}

	.cell-kicker {
		margin: 0 0 0.25rem;
		font-size: 0.58rem;
		font-weight: 800;
		letter-spacing: 0.16em;
		text-transform: uppercase;
		color: #54706f;
	}

	.cell-header h3 {
		margin: 0;
		font-size: 1.08rem;
		line-height: 1.2;
		color: #18252b;
	}

	.cell-subtitle {
		margin: 0.28rem 0 0;
		font-size: 0.72rem;
		color: #627174;
	}

	.cell-close {
		width: 1.85rem;
		height: 1.85rem;
		border: 0.0625rem solid rgba(31, 43, 52, 0.18);
		border-radius: 999rem;
		background: rgba(255, 255, 255, 0.72);
		color: #223138;
		font-size: 1.25rem;
		line-height: 1;
		cursor: pointer;
	}

	.cell-close:hover {
		background: white;
	}

	.cell-state {
		margin: 1rem;
		padding: 1rem;
		border-radius: 0.6rem;
		background: rgba(33, 102, 172, 0.08);
		color: #26485f;
		font-size: 0.86rem;
	}

	.cell-state.error {
		background: rgba(178, 24, 43, 0.08);
		color: #8d1c2a;
	}

	.cell-body {
		display: flex;
		flex-direction: column;
		gap: 0.7rem;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: 0.75rem;
		scrollbar-color: rgba(84, 112, 111, 0.45) transparent;
		scrollbar-width: thin;
	}

	.cell-body::-webkit-scrollbar {
		width: 0.625rem;
	}

	.cell-body::-webkit-scrollbar-thumb {
		background: rgba(84, 112, 111, 0.35);
		border: 0.1875rem solid transparent;
		border-radius: 999rem;
		background-clip: padding-box;
	}

	@media (max-width: 56.25rem) {
		.cell-inspector {
			width: min(82vw, calc(100% - 2rem));
		}
	}

	@media (max-width: 45rem) {
		.cell-inspector {
			inset: auto 0 0 0;
			width: 100%;
			max-height: 86%;
			border-radius: 0.9rem 0.9rem 0 0;
		}
	}
</style>
