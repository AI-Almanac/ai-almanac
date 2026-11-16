<script lang="ts">
	import type { GriddedEntry } from '$lib/workspace/gridded-file';

	let { entries }: { entries: GriddedEntry[] } = $props();
</script>

<section class="holds" aria-labelledby="gridded-contents-heading">
	<h4 id="gridded-contents-heading">What this file holds</h4>
	<ul>
		{#each entries as entry (entry.variables.join(' '))}
			<li>
				<span>{entry.label}</span>
				{#if entry.unit}<span class="unit">{entry.unit}</span>{/if}
			</li>
		{/each}
	</ul>
</section>

<style>
	.holds {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	h4 {
		margin: 0;
		font-size: var(--text-size-caption);
		font-weight: 600;
		color: var(--color-text-muted);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	li {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1) var(--space-2);
		align-items: baseline;
		font-size: var(--text-size-body);
	}
	.unit {
		font-size: var(--text-size-label);
		color: var(--color-text-dim);
	}
</style>
