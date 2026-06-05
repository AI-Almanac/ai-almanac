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
		color: #514c43;
		font-family: var(--font-body);
		font-size: 0.95rem;
		font-weight: 600;
		line-height: 1.42;
	}

	.empty {
		margin: 0;
		color: #736c60;
		font-family: var(--font-body);
		font-style: italic;
	}
</style>
