<script lang="ts">
	import type { ProductKind } from '$lib/workspace/products';

	interface Props {
		kind: ProductKind;
	}

	let { kind }: Props = $props();

	const PATHS: Record<ProductKind, string> = {
		benchmark: 'M3 20h18M6 20v-8M12 20V5M18 20v-11',
		blend: 'm12 4 9 4.5-9 4.5-9-4.5L12 4ZM3 13.5 12 18l9-4.5',
		forecast: 'm3 17 6-6 4 4 8-8M15 7h6v6',
		analysis: 'M4 5h16v14H4zM4 15l4-4 4 4 3-3 5 5'
	};
</script>

<svg viewBox="0 0 24 24" aria-hidden="true"><path d={PATHS[kind]} /></svg>

<style>
	svg {
		flex: none;
		width: 1.1rem;
		height: 1.1rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.75;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
</style>
