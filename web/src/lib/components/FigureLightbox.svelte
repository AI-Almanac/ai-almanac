<script lang="ts">
	import { fetchResultBlob } from '$lib/benchmarks.svelte';
	import type { ParsedFigure } from '$lib/result-parser';

	type Props = {
		figures: ParsedFigure[];
		index: number;
		onclose: () => void;
	};

	let { figures, index = $bindable(), onclose }: Props = $props();

	function prev() {
		if (index > 0) index--;
	}
	function next() {
		if (index < figures.length - 1) index++;
	}

	async function download() {
		const fig = figures[index];
		if (!fig) return;
		const { fetchResultBlob } = await import('$lib/benchmarks.svelte');
		const src = await fetchResultBlob(fig.raw.url);
		const a = document.createElement('a');
		a.href = src;
		a.download = fig.label.replace(/\s+/g, '_') + '.png';
		a.click();
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
		if (e.key === 'ArrowLeft') prev();
		if (e.key === 'ArrowRight') next();
	}

	$effect(() => {
		const fullscreenMaps = [...document.querySelectorAll('.map-root.fullscreen')];
		document.body.classList.add('figure-lightbox-open');
		for (const el of fullscreenMaps) {
			el.classList.add('obscured-by-lightbox');
		}
		document.addEventListener('keydown', onkeydown);
		return () => {
			document.body.classList.remove('figure-lightbox-open');
			for (const el of fullscreenMaps) {
				el.classList.remove('obscured-by-lightbox');
			}
			document.removeEventListener('keydown', onkeydown);
		};
	});
</script>

<div class="overlay">
	<button class="backdrop" onclick={onclose} aria-label="Close figure lightbox"></button>
	<div class="box" role="dialog" aria-modal="true" aria-label="Figure preview">
		<button class="close" onclick={onclose} aria-label="Close">&times;</button>
		<button class="download" onclick={download} aria-label="Download" title="Download figure"
			>&#x2B07;</button
		>

		{#if figures[index]}
			{@const fig = figures[index]}
			<div class="img-wrap">
				{#await fetchResultBlob(fig.raw.url)}
					<div class="loading">Loading…</div>
				{:then src}
					<img {src} alt={fig.label} />
				{:catch}
					<div class="loading">Failed to load image.</div>
				{/await}
			</div>
			<p class="caption">{fig.label}</p>
		{/if}

		<div class="nav">
			<button onclick={prev} disabled={index === 0}>&#8592; Prev</button>
			<span class="nav-counter">{index + 1} <span class="nav-of">of</span> {figures.length}</span>
			<button onclick={next} disabled={index === figures.length - 1}>Next &#8594;</button>
		</div>
	</div>
</div>

<style>
	.overlay {
		position: fixed;
		inset: 0;
		z-index: 2000;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 1.5rem;
	}

	.backdrop {
		position: absolute;
		inset: 0;
		border: 0;
		background: rgba(0, 0, 0, 0.85);
		cursor: zoom-out;
	}

	.box {
		position: relative;
		z-index: 1;
		max-width: min(90vw, 1200px);
		max-height: 90vh;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		background: var(--color-surface-raised);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.6rem;
		padding: 1.25rem;
		overflow: hidden;
	}

	.close,
	.download {
		position: absolute;
		top: 0.5rem;
		background: none;
		border: none;
		line-height: 1;
		cursor: pointer;
		color: var(--color-text-dim);
		padding: 0.15rem 0.4rem;
		border-radius: 0.3rem;
		transition:
			color 0.12s,
			background-color 0.12s;
	}

	.close {
		right: 0.75rem;
		font-size: 1.5rem;
	}
	.download {
		right: 2.75rem;
		font-size: 1.1rem;
	}

	.close:hover,
	.download:hover {
		color: var(--color-text);
		background: var(--color-border-subtle);
	}

	.nav-counter {
		font-weight: 600;
		color: var(--color-text);
		font-size: 0.85rem;
		min-width: 4rem;
		text-align: center;
	}

	.nav-of {
		font-weight: 400;
		color: var(--color-text-muted);
		font-size: 0.75rem;
	}

	.img-wrap {
		overflow: auto;
		display: flex;
		align-items: center;
		justify-content: center;
		flex: 1;
		min-height: 0;
	}

	.img-wrap img {
		max-width: 100%;
		max-height: calc(90vh - 9rem);
		display: block;
		border-radius: 0.3rem;
	}

	.loading {
		padding: 3rem;
		color: var(--color-text-dim);
		font-size: 0.9rem;
	}

	.caption {
		font-size: 0.8rem;
		color: var(--color-text-muted);
		margin: 0;
		text-align: center;
	}

	.nav {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 1.5rem;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}

	.nav button {
		background: none;
		border: 1px solid var(--color-border-subtle);
		color: var(--color-text-muted);
		border-radius: 0.3rem;
		padding: 0.25rem 0.7rem;
		cursor: pointer;
		font-size: 0.8rem;
		transition:
			background-color 0.12s,
			color 0.12s;
	}

	.nav button:hover:not(:disabled) {
		background: var(--color-accent-light);
		color: var(--color-accent);
	}

	.nav button:disabled {
		opacity: 0.35;
		cursor: not-allowed;
	}
</style>
