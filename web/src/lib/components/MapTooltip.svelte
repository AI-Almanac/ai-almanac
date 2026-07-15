<script lang="ts">
	import type { Snippet } from 'svelte';

	// Shared floating tooltip shell for the map surfaces: fixed dark-glass chrome
	// positioned at the cursor, with a standard coordinate header. Each map fills
	// the body via the children snippet, so the shell stays the single source of
	// truth for tooltip look-and-feel.
	type Props = {
		x: number;
		y: number;
		coords?: string;
		children?: Snippet;
	};
	let { x, y, coords, children }: Props = $props();
</script>

<div class="map-tooltip" style="left: {x}px; top: {y}px">
	{#if coords}<span class="tt-coords">{coords}</span>{/if}
	{#if children}{@render children()}{/if}
</div>

<style>
	.map-tooltip {
		position: absolute;
		z-index: 10;
		pointer-events: none;
		border: 1px solid rgba(255, 255, 255, 0.12);
		border-radius: 0.4rem;
		background: rgba(13, 17, 23, 0.9);
		backdrop-filter: blur(6px);
		padding: 0.35rem 0.6rem;
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		white-space: nowrap;
	}

	.tt-coords {
		font-size: 0.72rem;
		font-weight: 700;
		color: #8a8278;
		letter-spacing: 0.02em;
	}
</style>
