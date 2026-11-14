<script lang="ts">
	import type { WorkspaceArtifact } from '$lib/api';
	import FileRow from './FileRow.svelte';

	interface Props {
		files: WorkspaceArtifact[];
		onOpen: (file: WorkspaceArtifact) => void;
		onDownload: (file: WorkspaceArtifact) => void;
		onRename?: (file: WorkspaceArtifact, title: string | null) => Promise<void>;
	}

	let { files, onOpen, onDownload, onRename }: Props = $props();

	let expanded = $state(false);
	const count = $derived(files.length);
</script>

<div class="supporting" class:expanded>
	<button
		type="button"
		class="toggle"
		aria-expanded={expanded}
		onclick={() => (expanded = !expanded)}
	>
		<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
		{count} supporting {count === 1 ? 'file' : 'files'}
	</button>
	{#if expanded}
		<ul>
			{#each files as file (file.id)}
				<FileRow {file} {onOpen} {onDownload} {onRename} />
			{/each}
		</ul>
	{/if}
</div>

<style>
	.supporting {
		display: flex;
		flex-direction: column;
	}
	.toggle {
		display: inline-flex;
		align-self: flex-start;
		align-items: center;
		gap: 0.2rem;
		padding: 0.15rem 0.4rem 0.15rem 0.2rem;
		border: 0;
		border-radius: 0.25rem;
		background: none;
		color: var(--color-text-dim);
		font: inherit;
		font-size: 0.74rem;
		cursor: pointer;
	}
	.toggle:hover,
	.toggle:focus-visible {
		color: var(--color-text);
	}
	svg {
		width: 0.8rem;
		height: 0.8rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 2;
		stroke-linecap: round;
		stroke-linejoin: round;
		transition: transform 120ms ease;
	}
	.expanded svg {
		transform: rotate(90deg);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
	}
</style>
