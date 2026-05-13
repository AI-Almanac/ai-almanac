<script lang="ts">
	import { fetchResultBlob } from '$lib/benchmarks.svelte';
	import type { ParsedFigure } from '$lib/result-parser';

	type Props = {
		figure: ParsedFigure;
		onclick: () => void;
	};

	let { figure, onclick }: Props = $props();

	async function download() {
		const src = await fetchResultBlob(figure.raw.url);
		const a = document.createElement('a');
		a.href = src;
		a.download = figure.label.replace(/\s+/g, '_') + '.png';
		a.click();
	}

	const WINDOW_LABELS: Record<string, string> = { '1-15': 'Days 1–15', '16-30': 'Days 16–30' };
</script>

<div class="card">
	<div class="card-header">
		<span class="card-label">{figure.label}</span>
		<div class="badges">
			{#if figure.window}
				<span class="badge window">{WINDOW_LABELS[figure.window] ?? figure.window}</span>
			{/if}
			{#if figure.model}
				<span class="badge model">{figure.model.toUpperCase()}</span>
			{/if}
		</div>
	</div>

	<div class="img-wrap">
		{#await fetchResultBlob(figure.raw.url)}
			<div class="loading">Loading…</div>
		{:then src}
			<button
				class="expand-btn"
				{onclick}
				title="Click to expand"
				aria-label="Expand {figure.label}"
			>
				<img {src} alt={figure.label} />
			</button>
			<div class="hover-actions">
				<div class="expand-hint">&#x26F6; Expand</div>
				<button
					class="download-btn"
					onclick={(e) => {
						e.stopPropagation();
						download();
					}}
					aria-label="Download {figure.label}"
					title="Download figure"
				>
					&#x2B07;
				</button>
			</div>
		{:catch}
			<div class="loading">Failed to load image.</div>
		{/await}
	</div>
</div>

<style>
	.card {
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.5rem;
		overflow: hidden;
		background: var(--color-bg);
	}

	.card-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.6rem 0.9rem;
		border-bottom: 1px solid var(--color-border-subtle);
		background: var(--color-surface);
	}

	.card-label {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--color-text-muted);
	}

	.badges {
		display: flex;
		gap: 0.35rem;
		align-items: center;
	}

	.badge {
		font-size: 0.6rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.1rem 0.4rem;
		border-radius: 0.2rem;
	}

	.badge.window {
		background: var(--color-accent-light);
		color: var(--color-accent);
	}

	.badge.model {
		background: var(--color-border-subtle);
		color: var(--color-text-dim);
	}

	.img-wrap {
		position: relative;
	}

	.expand-btn {
		cursor: zoom-in;
		display: block;
		width: 100%;
		padding: 0;
		border: 0;
		background: transparent;
		color: inherit;
		font: inherit;
		text-align: inherit;
	}

	.expand-btn img {
		width: 100%;
		display: block;
	}

	.hover-actions {
		position: absolute;
		bottom: 0.5rem;
		right: 0.5rem;
		display: flex;
		gap: 0.3rem;
		opacity: 0;
		transition: opacity 0.15s;
	}

	.img-wrap:hover .hover-actions {
		opacity: 1;
	}

	.expand-hint,
	.download-btn {
		background: rgba(0, 0, 0, 0.55);
		color: #fff;
		font-size: 0.65rem;
		padding: 0.2rem 0.45rem;
		border-radius: 0.25rem;
	}

	.download-btn {
		cursor: pointer;
		pointer-events: all;
		border: 0;
		font: inherit;
	}

	.download-btn:hover {
		background: rgba(0, 0, 0, 0.8);
	}

	.loading {
		padding: 2rem;
		text-align: center;
		color: var(--color-text-dim);
		font-size: 0.85rem;
	}
</style>
