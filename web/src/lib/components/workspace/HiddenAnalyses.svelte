<script lang="ts">
	import { failureMessage } from '$lib/api';
	import type { AnalysisProduct } from '$lib/workspace/products';

	interface Props {
		/** The analyses the user hid from the products list. */
		analyses: AnalysisProduct[];
		onUnhide: (turnId: string) => Promise<void>;
	}

	let { analyses, onUnhide }: Props = $props();

	let open = $state(false);
	let failure = $state<string | null>(null);

	async function unhide(turnId: string) {
		failure = null;
		try {
			await onUnhide(turnId);
		} catch (e) {
			failure = failureMessage(e);
		}
	}
</script>

<section class="hidden-analyses">
	<button type="button" class="toggle" aria-expanded={open} onclick={() => (open = !open)}>
		Hidden analyses ({analyses.length})
	</button>
	{#if open}
		<ul class="list">
			{#each analyses as product (product.key)}
				{@const turnId = product.analysis.turnId}
				<li class="entry">
					<span class="title" title={product.heading.question ?? product.name}>{product.name}</span>
					{#if turnId}
						<button
							type="button"
							class="link"
							aria-label={`Show ${product.name} in the list again`}
							onclick={() => unhide(turnId)}
						>
							Unhide
						</button>
					{/if}
				</li>
			{/each}
		</ul>
		{#if failure}<p class="failure" role="alert">{failure}</p>{/if}
	{/if}
</section>

<style>
	.hidden-analyses {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		padding: 0 var(--space-2);
	}
	.toggle,
	.link {
		align-self: flex-start;
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		font-size: var(--text-size-caption);
		cursor: pointer;
	}
	.toggle:hover,
	.toggle:focus-visible,
	.link:hover,
	.link:focus-visible {
		color: var(--color-accent);
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.entry {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		min-width: 0;
	}
	.title {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: var(--text-size-label);
		color: var(--color-text-dim);
	}
	.failure {
		margin: 0;
		font-size: var(--text-size-caption);
		color: var(--color-danger);
	}
</style>
