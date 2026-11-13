<script lang="ts">
	import {
		failureMessage,
		getArtifactProvenance,
		type ArtifactProvenance,
		type WorkspaceArtifact
	} from '$lib/api';
	import SegmentedTabs from '$lib/components/SegmentedTabs.svelte';
	import DownloadButton from './DownloadButton.svelte';
	import FileContents from './FileContents.svelte';
	import { valuesAreShares } from '$lib/workspace/area-table';
	import IntegrityStatus from './IntegrityStatus.svelte';
	import LineageView from './LineageView.svelte';
	import RenamableName from './RenamableName.svelte';
	import { integrityStatus } from '$lib/workspace/lineage';
	import { formatBytes, formatName } from '$lib/workspace/file-preview';
	import { parseFileSummary } from '$lib/workspace/file-summary';
	import { griddedFile } from '$lib/workspace/gridded-file';

	interface Props {
		workspaceId: string;
		file: WorkspaceArtifact;
		onDownload: (file: WorkspaceArtifact) => void;
		onOpenTurn?: (turnId: string) => void;
		onRename?: (file: WorkspaceArtifact, title: string | null) => Promise<void>;
		/** The interactive view of the run this file came from, e.g. "Skill maps". */
		mapView?: { name: string; onOpen: () => void };
	}

	let { workspaceId, file, onDownload, onOpenTurn, onRename, mapView }: Props = $props();

	const name = $derived(file.title || file.label);
	const facts = $derived(fileFacts(file));
	const gridded = $derived(griddedFile(file));

	function fileFacts(file: WorkspaceArtifact): string {
		const size = parseFileSummary(file.summary).sizeBytes;
		const made = new Date(file.created_at).toLocaleString([], {
			dateStyle: 'medium',
			timeStyle: 'short'
		});
		return [formatName(file.format), size === null ? null : formatBytes(size), `made ${made}`]
			.filter(Boolean)
			.join(' · ');
	}

	const TABS = [
		{ value: 'contents', label: 'Contents' },
		{ value: 'lineage', label: 'How this was made' }
	];
	let tab = $state('contents');
	let provenance = $state<ArtifactProvenance | null>(null);
	let busy = $state(false);
	let error = $state<string | null>(null);

	const integrity = $derived(integrityStatus(provenance));
	// A derived id, so a live refresh of the same file does not re-run the check.
	const fileId = $derived(file.id);

	// Checking only re-hashes stored files, so it is cheap enough to run on every view.
	async function checkProvenance() {
		busy = true;
		error = null;
		try {
			provenance = await getArtifactProvenance(workspaceId, fileId, true);
		} catch (e) {
			error = failureMessage(e);
		} finally {
			busy = false;
		}
	}

	$effect(() => {
		checkProvenance();
	});
</script>

<div class="file-detail">
	<header>
		<div class="heading">
			<div class="title-row">
				<RenamableName {name} onRename={onRename && ((title) => onRename(file, title))}>
					{#snippet children(rename)}
						<h3>{name}</h3>
						{#if rename}{@render rename()}{/if}
					{/snippet}
				</RenamableName>
			</div>
			<span class="facts">{facts}</span>
			<IntegrityStatus status={integrity} checking={busy} onCheck={checkProvenance} />
		</div>
		<DownloadButton label={name} withText onclick={() => onDownload(file)} />
	</header>

	{#if gridded}<p class="what">{gridded.description}</p>{/if}

	{#if mapView}
		<button type="button" class="open-map" onclick={mapView.onOpen}>
			Explore this on the {mapView.name.toLowerCase()} →
		</button>
	{/if}

	<SegmentedTabs
		options={TABS}
		value={tab}
		onSelect={(next) => (tab = next)}
		ariaLabel="File view"
	/>

	<div class="panel">
		{#if tab === 'contents'}
			<FileContents {workspaceId} {file} shares={valuesAreShares(provenance)} />
		{:else}
			<LineageView {workspaceId} {provenance} {error} {onOpenTurn} />
		{/if}
	</div>
</div>

<style>
	.file-detail {
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
	}
	header {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: flex-start;
		gap: 0.75rem;
	}
	.heading {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		min-width: 0;
	}
	.title-row {
		display: flex;
		align-items: baseline;
		gap: 0.4rem;
		min-width: 0;
		font-size: 0.98rem;
		font-weight: 600;
	}
	.title-row :global(.rename) {
		font-weight: 400;
	}
	@media (hover: hover) {
		.title-row :global(.rename) {
			visibility: hidden;
		}
		.title-row:hover :global(.rename),
		.title-row:focus-within :global(.rename) {
			visibility: visible;
		}
	}
	h3 {
		margin: 0;
		font-size: 0.98rem;
		overflow-wrap: anywhere;
	}
	.facts {
		font-size: 0.76rem;
		color: var(--color-text-dim);
	}
	.what {
		margin: 0;
		max-width: 42rem;
		font-size: var(--text-size-body);
	}
	.panel {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		padding: 0 0.1rem;
	}
	.open-map {
		align-self: flex-start;
		padding: 0.35rem 0.7rem;
		border: 1px solid var(--color-accent-border);
		border-radius: 0.35rem;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font: inherit;
		font-size: 0.85rem;
		cursor: pointer;
	}
	.open-map:hover {
		background: var(--color-accent-soft);
	}
</style>
