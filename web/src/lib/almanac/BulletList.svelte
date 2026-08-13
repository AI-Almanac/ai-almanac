<script lang="ts">
	let { items }: { items: string[] } = $props();
</script>

{#if items.length}
	<ul class="bullet-list">
		{#each items as item}
			<li>{item}</li>
		{/each}
	</ul>
{:else}
	<p class="empty">To be filled</p>
{/if}

<style>
	.bullet-list {
		margin: 0;
		padding-left: 1.1rem;
		color: var(--color-text-muted);
		font-family: var(--font-body);
		font-size: 0.92rem;
		line-height: 1.5;
	}

	.empty {
		margin: 0;
		color: var(--color-text-dim);
		font-family: var(--font-body);
		font-style: italic;
	}
</style>
