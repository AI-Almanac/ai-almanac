<script lang="ts">
	import { autoUpdate, computePosition, flip, offset, shift } from 'svelte-floating-ui/dom';
	import type { CellMaePoint } from '$lib/api';

	type Props = {
		model: string;
		series: CellMaePoint[];
	};

	let { model, series }: Props = $props();

	let activePoint = $state<CellMaePoint | null>(null);
	let activeTarget = $state<HTMLElement | null>(null);
	let tooltipEl = $state<HTMLDivElement | null>(null);

	const detectedCount = $derived(series.filter((point) => point.model != null).length);

	function formatDays(value: number | null | undefined): string {
		return value == null ? 'not available' : `${value.toFixed(2)} days`;
	}

	function formatDelta(value: number | null | undefined): string {
		if (value == null) return 'not available';
		return `${value >= 0 ? '+' : ''}${value.toFixed(2)} days`;
	}

	function showTooltip(point: CellMaePoint, target: HTMLElement) {
		activePoint = point;
		activeTarget = target;
	}

	function hideTooltip() {
		activePoint = null;
		activeTarget = null;
	}

	function positionTooltip() {
		if (!activeTarget || !tooltipEl) return;
		computePosition(activeTarget, tooltipEl, {
			strategy: 'fixed',
			placement: 'top',
			middleware: [offset(8), flip(), shift({ padding: 8 })]
		}).then(({ x, y }) => {
			if (!tooltipEl) return;
			Object.assign(tooltipEl.style, {
				left: `${x}px`,
				top: `${y}px`
			});
		});
	}

	$effect(() => {
		if (!activeTarget || !tooltipEl) return;
		return autoUpdate(activeTarget, tooltipEl, positionTooltip);
	});
</script>

<div class="onset-years">
	<div class="onset-years-head">
		<span>Detected Onsets</span>
		<span>{detectedCount} / {series.length} years</span>
	</div>

	<div class="onset-year-grid" aria-label={`${model} detected onsets by year`}>
		{#each series as point}
			{@const detected = point.model != null}
			<button
				type="button"
				class="onset-year"
				class:detected
				aria-label={`${point.year}: ${detected ? 'onset detected' : 'no onset detected'}`}
				onmouseenter={(event) => showTooltip(point, event.currentTarget)}
				onfocus={(event) => showTooltip(point, event.currentTarget)}
				onmouseleave={hideTooltip}
				onblur={hideTooltip}
			></button>
		{/each}
	</div>

	<div class="onset-year-range">
		<span>{series[0]?.year}</span>
		<span>{series.at(-1)?.year}</span>
	</div>
</div>

{#if activePoint}
	<div class="onset-tooltip" bind:this={tooltipEl} role="tooltip">
		<strong>{activePoint.year}</strong>
		<span>{activePoint.model == null ? 'No model onset detected' : 'Model onset detected'}</span>
		<dl>
			<div>
				<dt>Model MAE</dt>
				<dd>{formatDays(activePoint.model)}</dd>
			</div>
			<div>
				<dt>Traditional Climatology MAE</dt>
				<dd>{formatDays(activePoint.baseline)}</dd>
			</div>
			<div>
				<dt>Delta</dt>
				<dd>{formatDelta(activePoint.delta)}</dd>
			</div>
		</dl>
	</div>
{/if}

<style>
	.onset-years {
		margin: 0 0.75rem 0.75rem;
		padding: 0.55rem 0.6rem 0.6rem;
		border: 0.0625rem solid rgba(31, 43, 52, 0.08);
		border-radius: 0.6rem;
		background: linear-gradient(180deg, #fbfdfc, #f3f7f5);
	}

	.onset-years-head,
	.onset-year-range {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.onset-years-head {
		margin-bottom: 0.45rem;
		color: #25363c;
		font-size: 0.68rem;
		font-weight: 800;
	}

	.onset-years-head span:last-child,
	.onset-year-range {
		color: #687679;
		font-family: var(--font-mono);
		font-size: 0.62rem;
		font-weight: 600;
	}

	.onset-year-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(0.72rem, 1fr));
		gap: 0.18rem;
	}

	.onset-year {
		aspect-ratio: 1;
		min-width: 0;
		border: 0.0625rem solid rgba(84, 112, 111, 0.16);
		border-radius: 0.18rem;
		background: #e4ebe8;
		cursor: default;
	}

	.onset-year:hover,
	.onset-year:focus-visible {
		border-color: rgba(31, 43, 52, 0.34);
		outline: none;
		box-shadow: 0 0 0 0.12rem rgba(33, 102, 172, 0.14);
	}

	.onset-year.detected {
		border-color: rgba(15, 118, 110, 0.2);
		background: #0f766e;
		box-shadow: inset 0 0 0 0.0625rem rgba(255, 255, 255, 0.28);
	}

	.onset-year.detected:hover,
	.onset-year.detected:focus-visible {
		border-color: rgba(15, 118, 110, 0.7);
		box-shadow:
			inset 0 0 0 0.0625rem rgba(255, 255, 255, 0.34),
			0 0 0 0.12rem rgba(15, 118, 110, 0.16);
	}

	.onset-year-range {
		margin-top: 0.35rem;
	}

	.onset-tooltip {
		position: fixed;
		z-index: 1000;
		max-width: min(18rem, calc(100vw - 1.5rem));
		padding: 0.55rem 0.65rem;
		border: 0.0625rem solid rgba(218, 232, 226, 0.95);
		border-radius: 0.5rem;
		background: rgba(19, 31, 36, 0.96);
		box-shadow: 0 0.75rem 2.25rem rgba(3, 14, 25, 0.32);
		color: #f7fbfa;
		font-size: 0.72rem;
		pointer-events: none;
	}

	.onset-tooltip strong,
	.onset-tooltip span {
		display: block;
	}

	.onset-tooltip strong {
		margin-bottom: 0.1rem;
		font-family: var(--font-mono);
		font-size: 0.78rem;
	}

	.onset-tooltip span {
		margin-bottom: 0.45rem;
		color: #d7e3df;
	}

	.onset-tooltip dl {
		display: grid;
		gap: 0.25rem;
		margin: 0;
	}

	.onset-tooltip div {
		display: flex;
		justify-content: space-between;
		gap: 0.8rem;
	}

	.onset-tooltip dt,
	.onset-tooltip dd {
		margin: 0;
	}

	.onset-tooltip dt {
		color: #aebfbc;
	}

	.onset-tooltip dd {
		font-family: var(--font-mono);
		color: #ffffff;
	}
</style>
