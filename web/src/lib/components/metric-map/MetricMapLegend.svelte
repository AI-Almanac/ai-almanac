<script lang="ts">
	import { parseKey } from './layerKeys';
	import { isHigherBetterMetric, isNeutralDeltaMetric, modelDisplayName } from './mapUi';
	import type { LayerState, MapViewMode } from './types';

	type VisibleLayer = LayerState & { key: string };

	type Props = {
		visibleLayers: VisibleLayer[];
		viewMode: MapViewMode;
		metricLabel: (metricValue: string) => string;
		windowLabelFor: (windowValue: string | undefined) => string;
	};

	let { visibleLayers, viewMode, metricLabel, windowLabelFor }: Props = $props();
</script>

{#if visibleLayers.length > 0}
	<div class="legend">
		{#if viewMode === 'swipe' && visibleLayers.length === 2}
			{@const vl = visibleLayers[0]}
			{@const { metric: vMetric } = parseKey(vl.key)}
			{@const gradient = `linear-gradient(to right, ${vl.stops.join(', ')})`}
			<div class="legend-title">
				{metricLabel(vMetric)}
				<span class="legend-delta-badge">Shared swipe scale</span>
			</div>
			<div class="scale-bar" style="background: {gradient}"></div>
			<div class="scale-labels">
				<span>{vl.data.min.toFixed(2)}</span>
				<span class="scale-unit">({vl.data.unit})</span>
				<span>{vl.data.max.toFixed(2)}</span>
			</div>
		{:else}
			{#each visibleLayers as vl, i}
				{@const {
					modelName: vModel,
					metric: vMetric,
					window: vWindow,
					referenceWindow
				} = parseKey(vl.key)}
				{@const gradient = `linear-gradient(to right, ${vl.stops.join(', ')})`}
				{@const displayName = modelDisplayName(vModel)}
				{@const referenceName = vl.referenceModelName
					? modelDisplayName(vl.referenceModelName)
					: null}
				{#if i > 0}<div class="legend-divider"></div>{/if}
				<div class="legend-title">
					{displayName} · {windowLabelFor(vWindow)} — {metricLabel(vMetric)}
					{#if vl.isDelta && referenceName}
						<span class="legend-delta-badge"
							>Δ vs {referenceName} · {windowLabelFor(referenceWindow)}</span
						>
					{/if}
				</div>
				<div class="scale-bar" style="background: {gradient}"></div>
				{#if vl.isDelta && vl.deltaMaxAbs != null}
					<div class="scale-labels">
						<span>−{vl.deltaMaxAbs.toFixed(3)}</span>
						<span class="scale-unit"
							>{isNeutralDeltaMetric(vMetric)
								? '(negative)'
								: isHigherBetterMetric(vMetric)
									? '(worse)'
									: '(better)'}</span
						>
						<span>0</span>
						<span class="scale-unit"
							>{isNeutralDeltaMetric(vMetric)
								? '(positive)'
								: isHigherBetterMetric(vMetric)
									? '(better)'
									: '(worse)'}</span
						>
						<span>+{vl.deltaMaxAbs.toFixed(3)}</span>
					</div>
				{:else}
					<div class="scale-labels">
						<span>{vl.data.min.toFixed(2)}</span>
						<span class="scale-unit">({vl.data.unit})</span>
						<span>{vl.data.max.toFixed(2)}</span>
					</div>
				{/if}
			{/each}
		{/if}
	</div>
{/if}

<style>
	.legend {
		position: absolute;
		bottom: 1rem;
		left: 50%;
		transform: translateX(-50%);
		z-index: 20;
		background: rgba(255, 255, 255, 0.92);
		padding: 0.45rem 0.6rem;
		border-radius: 0.4rem;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
		border: 1px solid #ccc;
		width: min(18rem, calc(100% - 7rem));
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
	}

	:global(body.figure-lightbox-open .map-root) .legend,
	:global(.map-root.fullscreen.obscured-by-lightbox) .legend {
		display: none;
	}

	.legend-title {
		font-size: 0.54rem;
		font-weight: 700;
		color: #333;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		margin-bottom: 0.22rem;
		text-align: center;
	}

	.legend-divider {
		height: 1px;
		background: #e5e5e5;
		margin: 0.4rem 0;
	}

	.legend-delta-badge {
		display: inline-block;
		font-size: 0.46rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: #555;
		background: #eee;
		border: 1px solid #ccc;
		padding: 0.05rem 0.3rem;
		border-radius: 0.2rem;
		margin-left: 0.3rem;
		vertical-align: middle;
	}

	.scale-bar {
		height: 8px;
		border-radius: 2px;
		margin-bottom: 0.15rem;
	}

	.scale-labels {
		display: flex;
		justify-content: space-between;
		font-size: 0.54rem;
		font-family: var(--font-mono);
		color: #555;
	}

	.scale-unit {
		color: #999;
		font-size: 0.52rem;
	}
</style>
