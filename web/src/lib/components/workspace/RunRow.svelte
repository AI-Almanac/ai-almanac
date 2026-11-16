<script lang="ts">
	import type { RunProduct } from '$lib/workspace/products';
	import { runHeadline, runView } from '$lib/workspace/runs';
	import RunStatusIndicator from './RunStatusIndicator.svelte';

	interface Props {
		product: RunProduct;
		onOpen: (product: RunProduct) => void;
		/** Whether the stage is showing this run. */
		selected?: boolean;
	}

	let { product, onOpen, selected = false }: Props = $props();

	const view = $derived(runView(product.run));
	const headline = $derived(runHeadline(product.run));
	const openLabel = $derived(
		view ? `Open the ${view.toLowerCase()}: ${product.name}` : product.name
	);
</script>

<li class="run" class:selected>
	<button
		type="button"
		class="open"
		title={openLabel}
		aria-current={selected ? 'true' : undefined}
		onclick={() => onOpen(product)}
	>
		<span class="title">{product.label}</span>
		{#if headline}<span class="headline">{headline}</span>{/if}
	</button>
	<RunStatusIndicator run={product.run} quiet />
</li>

<style>
	.run {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		padding: var(--space-1) var(--space-2);
		border-radius: 0.3rem;
	}
	.run:hover {
		background: var(--color-surface-muted);
	}
	.selected {
		background: var(--color-accent-light);
	}
	.selected .title,
	.open:hover .title {
		color: var(--color-accent);
	}
	.open {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-width: 0;
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	/* The parts that tell runs apart come first, so two lines keep them in view. */
	.title {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		overflow: hidden;
		overflow-wrap: anywhere;
		font-size: var(--text-size-label);
		font-weight: 500;
	}
	.headline {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
	}
</style>
