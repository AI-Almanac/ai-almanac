<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { ProductGroup, ProductKind } from '$lib/workspace/products';
	import ProductKindIcon from './ProductKindIcon.svelte';

	interface Props {
		groups: ProductGroup[];
		/** Opens the list at the kind that was pressed. */
		onExpand: (kind: ProductKind) => void;
		/** The "New" menu, given whether to draw it small. */
		newRun?: Snippet<[compact: boolean]>;
	}

	let { groups, onExpand, newRun }: Props = $props();
	let kinds = $state<HTMLElement | null>(null);

	/** For a host that collapses the list and hands the keyboard to what reopens it. */
	export function focus() {
		kinds?.querySelector('button')?.focus();
	}
</script>

<!-- The collapsed list: how much the workspace holds, one kind at a time. -->
<nav class="strip" aria-label="Products">
	{@render newRun?.(true)}
	<div class="kinds" bind:this={kinds}>
		{#each groups as group (group.kind)}
			{@const label = `${group.title}: ${group.products.length}. Show the products list`}
			<button
				type="button"
				class="kind"
				aria-label={label}
				title={label}
				onclick={() => onExpand(group.kind)}
			>
				<ProductKindIcon kind={group.kind} />
				<span class="count">{group.products.length}</span>
			</button>
		{/each}
	</div>
</nav>

<style>
	/* The menu sits outside the scrolling kinds so its list is not clipped by them. */
	.strip,
	.kinds {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-2);
		min-height: 0;
	}
	.strip {
		flex: 1;
		padding: var(--space-3) 0;
	}
	.kinds {
		flex: 1;
		overflow-y: auto;
	}
	.kind {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0;
		padding: var(--space-1) var(--space-2);
		border: 0;
		border-radius: 0.35rem;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		cursor: pointer;
	}
	.kind:hover,
	.kind:focus-visible {
		background: var(--color-surface-muted);
		color: var(--color-accent);
	}
	.count {
		font-size: var(--text-size-caption);
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	@media (max-width: 1050px) {
		/* The zones stack at this width, so the strip lies along the top of its row. */
		.strip,
		.kinds {
			flex-direction: row;
			justify-content: flex-start;
		}
		.strip {
			padding: var(--space-1) var(--space-3);
		}
		.kind {
			flex-direction: row;
			gap: var(--space-1);
		}
	}
</style>
