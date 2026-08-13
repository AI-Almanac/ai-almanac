<script lang="ts">
	let { items }: { items: string[] } = $props();
</script>

{#if items.length}
	<ul class="tag-list">
		{#each items as item}
			<li>{item}</li>
		{/each}
	</ul>
{:else}
	<p class="empty">To be filled</p>
{/if}

<style>
	.tag-list {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 8rem), 1fr));
		gap: 0;
		margin: 0;
		padding-left: 0;
		border-top: 0.0625rem solid var(--color-border-subtle);
		list-style: none;
	}

	.tag-list li {
		border-bottom: 0.0625rem solid var(--color-border-subtle);
		color: var(--color-text);
		padding: 0.28rem 0.55rem 0.28rem 0;
		font-family: var(--font-body);
		font-size: 0.88rem;
		font-weight: 600;
	}

	.empty {
		margin: 0;
		color: var(--color-text-dim);
		font-family: var(--font-body);
		font-style: italic;
	}
</style>
