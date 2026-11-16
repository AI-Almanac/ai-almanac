<script lang="ts">
	import type { WorkspaceArtifact } from '$lib/api';
	import type { Analysis } from '$lib/workspace/analyses';
	import { foldSupporting } from '$lib/workspace/supporting';

	interface Props {
		analysis: Analysis;
		/** The file the stage is showing. */
		shownId: string;
		onOpen: (file: WorkspaceArtifact) => void;
	}

	let { analysis, shownId, onOpen }: Props = $props();

	const folded = $derived(foldSupporting(analysis.files));
</script>

{#snippet fileButton(file: WorkspaceArtifact, supporting: boolean)}
	<li>
		<button
			type="button"
			class="file"
			class:supporting
			aria-current={file.id === shownId ? 'true' : undefined}
			title={supporting ? 'A file this analysis was made from' : undefined}
			onclick={() => onOpen(file)}
		>
			{file.title || file.label}
		</button>
	</li>
{/snippet}

<!-- An analysis can make several files; the stage shows one, so the rest are a press away. -->
<nav class="analysis-files" aria-label="Files in this analysis">
	<ul>
		{#each folded.results as file (file.id)}
			{@render fileButton(file, false)}
		{/each}
		{#each folded.supporting as file (file.id)}
			{@render fileButton(file, true)}
		{/each}
	</ul>
</nav>

<style>
	ul {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.file {
		max-width: 16rem;
		overflow: hidden;
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.35rem;
		background: none;
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-label);
		text-overflow: ellipsis;
		white-space: nowrap;
		cursor: pointer;
	}
	.file:hover,
	.file:focus-visible {
		border-color: var(--color-accent-border);
		color: var(--color-accent);
	}
	.file[aria-current='true'] {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
	}
	.supporting {
		color: var(--color-text-muted);
	}
</style>
