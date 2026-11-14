<script lang="ts">
	import {
		findProducts,
		type AnalysisProduct,
		type Product,
		type ProductGroup,
		type ProductKind
	} from '$lib/workspace/products';
	import type { Snippet } from 'svelte';
	import AnalysisEntry from './AnalysisEntry.svelte';
	import RunRow from './RunRow.svelte';
	import ProductKindIcon from './ProductKindIcon.svelte';
	import ZoneToggle from './ZoneToggle.svelte';

	interface Props {
		groups: ProductGroup[];
		/** The product the stage is showing, highlighted in the list. */
		shownKey?: string | null;
		/** The kind to bring into view when the list opens. */
		openAt?: ProductKind | null;
		onOpen: (product: Product) => void;
		onCollapse: () => void;
		onOpenTurn?: (turnId: string) => void;
		onRenameAnalysis?: (turnId: string, title: string | null) => Promise<void>;
		onRefresh?: () => void;
		/** The "New" menu, given whether to draw it small; left out when there is nothing to offer. */
		newRun?: Snippet<[compact: boolean]>;
	}

	let {
		groups,
		shownKey = null,
		openAt = null,
		onOpen,
		onCollapse,
		onOpenTurn,
		onRenameAnalysis,
		onRefresh,
		newRun
	}: Props = $props();

	let query = $state('');
	const found = $derived(findProducts(groups, query));

	// Scrolls the list itself: scrollIntoView would move the page too once the zones stack.
	function bringIntoView(section: HTMLElement, wanted: boolean) {
		const list = section.parentElement;
		if (wanted && list) list.scrollTop = section.offsetTop - list.offsetTop;
	}

	let collapse = $state<{ focus: () => void } | null>(null);

	/** For a host that opens the list and hands the keyboard to it. */
	export function focus() {
		collapse?.focus();
	}
</script>

<nav class="products" aria-label="Products">
	<header class="head">
		<h2>Products</h2>
		{#if onRefresh}<button type="button" class="link" onclick={onRefresh}>Refresh</button>{/if}
		<ZoneToggle
			bind:this={collapse}
			label="Collapse the products list"
			toward="left"
			onclick={onCollapse}
		/>
	</header>
	{#if newRun}<div class="new">{@render newRun(false)}</div>{/if}
	<input
		class="find"
		type="search"
		placeholder="Find a product"
		aria-label="Find a product"
		bind:value={query}
	/>

	<div class="groups">
		{#if found.length === 0}
			<p class="none">No product has “{query.trim()}” in its name.</p>
		{/if}
		{#each found as group (group.kind)}
			<section class="group" use:bringIntoView={group.kind === openAt}>
				<h3>
					<ProductKindIcon kind={group.kind} />
					{group.title}
					<span class="count">{group.products.length}</span>
				</h3>
				<ul class="list">
					{#each group.products as product (product.key)}
						{#if product.kind === 'analysis'}
							<AnalysisEntry
								{product}
								selected={product.key === shownKey}
								onOpen={(analysis: AnalysisProduct) => onOpen(analysis)}
								{onOpenTurn}
								onRename={onRenameAnalysis}
							/>
						{:else}
							<RunRow {product} selected={product.key === shownKey} {onOpen} />
						{/if}
					{/each}
				</ul>
			</section>
		{/each}
	</div>
</nav>

<style>
	.products {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: var(--space-2);
		min-width: 0;
		min-height: 0;
		padding: var(--space-3) var(--space-2);
	}
	.head {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding-left: var(--space-2);
	}
	.head h2 {
		flex: 1;
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-lead);
	}
	.new {
		display: flex;
		padding-left: var(--space-2);
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: var(--text-size-caption);
		cursor: pointer;
	}
	.find {
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface-raised);
		color: var(--color-text);
		font-size: var(--text-size-label);
	}
	.groups {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: var(--space-4);
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
	}
	.group {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	h3 {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		margin: 0;
		padding: 0 var(--space-2) var(--space-1);
		border-bottom: 1px solid var(--color-border);
		font-size: var(--text-size-caption);
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--color-text-muted);
	}
	.count {
		font-weight: 400;
		letter-spacing: 0;
		color: var(--color-text-dim);
	}
	.list {
		display: flex;
		flex-direction: column;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.none {
		margin: 0;
		padding: var(--space-2);
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
</style>
