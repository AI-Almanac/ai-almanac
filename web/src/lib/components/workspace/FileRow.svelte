<script lang="ts">
	import type { WorkspaceArtifact } from '$lib/api';
	import DownloadButton from './DownloadButton.svelte';
	import RenamableName from './RenamableName.svelte';

	interface Props {
		file: WorkspaceArtifact;
		onOpen: (file: WorkspaceArtifact) => void;
		onDownload: (file: WorkspaceArtifact) => void;
		onRename?: (file: WorkspaceArtifact, title: string | null) => Promise<void>;
	}

	let { file, onOpen, onDownload, onRename }: Props = $props();

	const name = $derived(file.title || file.label);
</script>

<li class="file-row">
	<RenamableName {name} onRename={onRename && ((title) => onRename(file, title))}>
		{#snippet children(rename)}
			<button type="button" class="file-open" title={file.label} onclick={() => onOpen(file)}>
				<span class="file-title">{name}</span>
				<span class="file-format">{file.format}</span>
			</button>
			<span class="file-actions">
				{#if rename}{@render rename()}{/if}
				<DownloadButton label={name} onclick={() => onDownload(file)} />
			</span>
		{/snippet}
	</RenamableName>
</li>

<style>
	.file-row {
		display: flex;
		align-items: center;
		border-radius: 0.3rem;
	}
	.file-row:hover {
		background: var(--color-surface-muted);
	}
	.file-open {
		display: flex;
		flex: 1;
		min-width: 0;
		align-items: baseline;
		gap: 0.5rem;
		padding: 0.2rem 0.4rem;
		border: 0;
		background: none;
		color: var(--color-text);
		font: inherit;
		font-size: 0.82rem;
		text-align: left;
		cursor: pointer;
	}
	.file-open:hover .file-title {
		color: var(--color-accent);
	}
	.file-title {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.file-format {
		flex: none;
		font-family: var(--font-mono);
		font-size: var(--text-size-caption);
		color: var(--color-text-dim);
	}
	.file-actions {
		display: flex;
		flex: none;
		align-items: center;
		gap: 0.1rem;
	}
	.file-row > :global(.name-field) {
		padding: 0.15rem 0.4rem;
		font-size: 0.82rem;
	}
	/* Row actions on every line are noise when scanning a list, so they wait for the
	   pointer; touch screens and keyboard focus still always reach them. */
	@media (hover: hover) {
		.file-actions {
			visibility: hidden;
		}
		.file-row:hover .file-actions,
		.file-row:focus-within .file-actions {
			visibility: visible;
		}
	}
</style>
