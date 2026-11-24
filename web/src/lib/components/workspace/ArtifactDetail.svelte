<script lang="ts">
	import { untrack } from 'svelte';
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
		/** What to call the file when its own title says too little, e.g. a script. */
		displayName?: string;
	}

	let { workspaceId, file, onDownload, onOpenTurn, onRename, mapView, displayName }: Props =
		$props();

	const name = $derived(displayName ?? (file.title || file.label));
	const facts = $derived(fileFacts(file));
	const gridded = $derived(griddedFile(file));

	function madeWhen(iso: string, now = new Date()): string {
		const made = new Date(iso);
		const thisYear = made.getFullYear() === now.getFullYear();
		return made.toLocaleString([], {
			month: 'short',
			day: 'numeric',
			year: thisYear ? undefined : 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		});
	}

	function fileFacts(file: WorkspaceArtifact): string {
		const size = parseFileSummary(file.summary).sizeBytes;
		const sizeText = size === null ? null : formatBytes(size);
		return [formatName(file.format), sizeText, madeWhen(file.created_at)]
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

	// Each check is numbered, so a slower answer for a file no longer shown is dropped.
	let latestCheck = 0;

	// Checking only re-hashes stored files, so it is cheap enough to run on every view.
	async function checkProvenance() {
		const check = ++latestCheck;
		busy = true;
		error = null;
		try {
			const checked = await getArtifactProvenance(workspaceId, fileId, true);
			if (check === latestCheck) provenance = checked;
		} catch (e) {
			if (check === latestCheck) error = failureMessage(e);
		} finally {
			if (check === latestCheck) busy = false;
		}
	}

	$effect(() => {
		void fileId;
		untrack(() => {
			// The last file's history says nothing about this one.
			provenance = null;
			checkProvenance();
		});
	});
</script>

<div class="file-detail">
	<header>
		<div class="title-row">
			<RenamableName {name} onRename={onRename && ((title) => onRename(file, title))}>
				{#snippet children(rename)}
					<h3>{name}</h3>
					{#if rename}{@render rename()}{/if}
				{/snippet}
			</RenamableName>
			<span class="download"><DownloadButton label={name} onclick={() => onDownload(file)} /></span>
		</div>
		<div class="meta-row">
			<p class="facts">
				<span>{facts}</span>
				<!-- A failed re-check leaves no earlier result standing in for it. -->
				<IntegrityStatus
					status={error ? null : integrity}
					checking={busy}
					onCheck={checkProvenance}
				/>
				{#if mapView}
					<button type="button" class="open-map" onclick={mapView.onOpen}>
						Open in {mapView.name.toLowerCase()} →
					</button>
				{/if}
			</p>
			<SegmentedTabs
				options={TABS}
				value={tab}
				onSelect={(next) => (tab = next)}
				ariaLabel="File view"
			/>
		</div>
		{#if gridded}<p class="what" title={gridded.description}>{gridded.description}</p>{/if}
	</header>

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
		gap: 0.75rem;
	}
	header {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
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
	.download {
		align-self: center;
		margin-left: auto;
	}
	.meta-row {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: center;
		gap: 0.4rem 1rem;
	}
	.facts {
		display: flex;
		flex: 1 1 auto;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.2rem 0;
		min-width: 0;
		margin: 0;
		font-size: 0.76rem;
		color: var(--color-text-dim);
	}
	.facts > :global(* + *)::before {
		content: '·';
		margin: 0 0.45em;
		color: var(--color-text-dim);
	}
	.open-map {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		white-space: nowrap;
		cursor: pointer;
	}
	.open-map:hover,
	.open-map:focus-visible {
		text-decoration: underline;
		text-underline-offset: 0.15em;
	}
	/* The description runs long for some files; two lines say what it is. */
	.what {
		display: -webkit-box;
		overflow: hidden;
		margin: 0;
		max-width: 42rem;
		font-size: 0.8rem;
		color: var(--color-text-muted);
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
	}
	.panel {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		padding: 0 0.1rem;
	}
</style>
