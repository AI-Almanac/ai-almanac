<!-- Stands in for FigureCard, which fetches its image: shows the label and forwards clicks. -->
<script lang="ts">
	import type { ParsedFigure } from '../../src/lib/result-parser';

	const { figure, onclick }: { figure: ParsedFigure; onclick: () => void } = $props();
</script>

<button {onclick}>{figure.label}</button>
