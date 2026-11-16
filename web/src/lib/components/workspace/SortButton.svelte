<script lang="ts">
	import type { SortDirection } from '$lib/workspace/table-sort';

	interface Props {
		/** The column's heading. */
		label: string;
		/** How the column is sorted now; the heading cell's `aria-sort` should say the same. */
		state: SortDirection | 'none';
		onSort: () => void;
	}

	let { label, state, onSort }: Props = $props();

	const ARROWS: Record<SortDirection | 'none', string> = {
		descending: '▼',
		ascending: '▲',
		none: ''
	};
</script>

<!-- A column heading that sorts the table by its column. -->
<button type="button" class="sort" title={`Sort by ${label}`} onclick={onSort}>
	{label}
	<span class="arrow" aria-hidden="true">{ARROWS[state]}</span>
</button>

<style>
	.sort {
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		font-weight: inherit;
		text-align: inherit;
		cursor: pointer;
	}
	.sort:hover {
		color: var(--color-accent);
	}
	.arrow {
		font-size: var(--text-size-caption);
	}
</style>
