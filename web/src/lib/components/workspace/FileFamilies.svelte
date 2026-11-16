<script lang="ts">
	import type { WorkspaceArtifact } from '$lib/api';
	import { groupByFamily } from '$lib/workspace/runs';
	import { foldSupporting } from '$lib/workspace/supporting';
	import FileRow from './FileRow.svelte';
	import SupportingFiles from './SupportingFiles.svelte';

	interface Props {
		files: WorkspaceArtifact[];
		onOpen: (file: WorkspaceArtifact) => void;
		onDownload: (file: WorkspaceArtifact) => void;
		onRename?: (file: WorkspaceArtifact, title: string | null) => Promise<void>;
	}

	let { files, onOpen, onDownload, onRename }: Props = $props();

	const folded = $derived(foldSupporting(files));
	const families = $derived(groupByFamily(folded.results));
</script>

<div class="families">
	{#each families as family (family.name)}
		<section class="family">
			<h4>{family.name} <span class="count">{family.files.length}</span></h4>
			<ul>
				{#each family.files as file (file.id)}
					<FileRow {file} {onOpen} {onDownload} {onRename} />
				{/each}
			</ul>
		</section>
	{/each}
	{#if folded.supporting.length > 0}
		<SupportingFiles files={folded.supporting} {onOpen} {onDownload} {onRename} />
	{/if}
</div>

<style>
	.families {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.family {
		display: flex;
		flex-direction: column;
	}
	h4 {
		margin: 0;
		padding: 0.1rem 0.4rem;
		font-size: var(--text-size-caption);
		font-weight: 600;
		color: var(--color-text-muted);
	}
	.count {
		margin-left: 0.2rem;
		font-weight: 400;
		color: var(--color-text-dim);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
	}
</style>
