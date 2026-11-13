<script lang="ts">
	import type { WorkspaceArtifact } from '$lib/api';
	import { parseFileSummary } from '$lib/workspace/file-summary';
	import { isAreaSummary } from '$lib/workspace/area-table';
	import { griddedFile } from '$lib/workspace/gridded-file';
	import { PREVIEW_MAX_BYTES, formatBytes, previewKind } from '$lib/workspace/file-preview';
	import AreaTable from './AreaTable.svelte';
	import CodeBlock from './CodeBlock.svelte';
	import CsvTable from './CsvTable.svelte';
	import FigurePreview from './FigurePreview.svelte';
	import FileDetails from './FileDetails.svelte';
	import FileText from './FileText.svelte';
	import GriddedContents from './GriddedContents.svelte';

	interface Props {
		workspaceId: string;
		file: WorkspaceArtifact;
		/** For an area summary: whether its values are one run's area shares, not chances. */
		shares?: boolean;
	}

	let { workspaceId, file, shares = false }: Props = $props();

	const kind = $derived(previewKind(file));
	const size = $derived(parseFileSummary(file.summary).sizeBytes);
	const tooLarge = $derived(kind !== 'figure' && size !== null && size > PREVIEW_MAX_BYTES);
	const gridded = $derived(griddedFile(file));
</script>

<div class="contents">
	{#if kind === 'figure'}
		<FigurePreview {workspaceId} {file} />
	{:else if tooLarge && size !== null}
		<p class="dim">
			At {formatBytes(size)} this file is too large to show here. Download it to open it.
		</p>
	{:else if kind === 'table' && isAreaSummary(file)}
		<FileText {workspaceId} fileId={file.id}>
			{#snippet children(text)}<AreaTable {text} {shares} />{/snippet}
		</FileText>
	{:else if kind === 'table'}
		<FileText {workspaceId} fileId={file.id}>
			{#snippet children(text)}<CsvTable {text} />{/snippet}
		</FileText>
	{:else if kind === 'code' || kind === 'text'}
		<FileText {workspaceId} fileId={file.id}>
			{#snippet children(text)}<CodeBlock code={text} />{/snippet}
		</FileText>
	{/if}
	{#if gridded}<GriddedContents entries={gridded.entries} />{/if}
	<FileDetails {file} />
</div>

<style>
	.contents {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.dim {
		margin: 0;
		color: var(--color-text-dim);
		font-size: 0.84rem;
	}
</style>
