<script lang="ts">
	import type { ChatSourceArtifact } from '$lib/api';
	import OpenItemCard from '$lib/components/chat/OpenItemCard.svelte';
	import { sourceCard, sourcesSummary } from '$lib/chat/reply-cards';

	interface Props {
		sources: ChatSourceArtifact[];
		onOpen: (source: ChatSourceArtifact) => void;
		/** Current names of the workspace's files by id, so references show what the user calls them. */
		fileTitles?: Record<string, string>;
	}

	const { sources, onOpen, fileTitles = {} }: Props = $props();

	let expanded = $state(false);
</script>

<footer class="sources">
	<button
		type="button"
		class="summary"
		aria-expanded={expanded}
		onclick={() => (expanded = !expanded)}
	>
		<span>{sourcesSummary(sources.length)}</span>
		<span class="chevron" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
	</button>
	{#if expanded}
		<ul class="list">
			{#each sources as source (source.id)}
				<li>
					<OpenItemCard card={sourceCard(source, fileTitles)} onOpen={() => onOpen(source)} />
				</li>
			{/each}
		</ul>
	{/if}
</footer>

<style>
	.sources {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.summary {
		display: inline-flex;
		align-items: center;
		align-self: flex-start;
		gap: var(--space-1);
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		font-size: var(--text-size-caption);
		cursor: pointer;
	}
	.summary:hover {
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
</style>
