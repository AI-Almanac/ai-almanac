<script lang="ts">
	import {
		downloadWorkspaceArtifact,
		failureMessage,
		type WorkspaceArtifact,
		type WorkspaceDetail
	} from '$lib/api';
	import { backLink, type CanvasView } from '$lib/workspace/canvas-view';
	import {
		defaultRun,
		metricsWindow,
		runKind,
		runOf,
		runTitle,
		runView,
		RUN_KIND_LABEL,
		type Run,
		type WorkspaceResults
	} from '$lib/workspace/runs';
	import ArtifactDetail from './ArtifactDetail.svelte';
	import RunDetail from './RunDetail.svelte';
	import RunStatusIndicator from './RunStatusIndicator.svelte';

	interface Props {
		workspace: WorkspaceDetail;
		/** The workspace's runs and analyses, grouped once by the page. */
		results: WorkspaceResults;
		view: CanvasView;
		onNavigate: (view: CanvasView) => void;
		onOpenTurn?: (turnId: string) => void;
		onRenameFile?: (file: WorkspaceArtifact, title: string | null) => Promise<void>;
	}

	let { workspace, results, view, onNavigate, onOpenTurn, onRenameFile }: Props = $props();

	let downloadError = $state<string | null>(null);

	// The stage lands on the newest run until one is chosen.
	const openRun = $derived(
		view.kind === 'run'
			? (results.runs.find((run) => run.key === view.runKey) ?? null)
			: view.kind === 'overview'
				? defaultRun(results.runs)
				: null
	);
	const back = $derived(backLink(view, results.runs.length > 0));
	const openFile = $derived(
		view.kind === 'file'
			? (workspace.artifacts.find((file) => file.id === view.artifactId) ?? null)
			: null
	);

	/** Where a file can be explored: its run's view, on the file's own lead-time window. */
	function mapViewOf(file: WorkspaceArtifact) {
		const run = runOf(file, results.runs);
		const name = run ? runView(run) : null;
		if (!run || !name) return undefined;
		const window = metricsWindow(file) ?? undefined;
		return { name, onOpen: () => onNavigate({ kind: 'run', runKey: run.key, window }) };
	}

	function showFile(file: WorkspaceArtifact, fromRun: Run | null = null) {
		onNavigate({ kind: 'file', artifactId: file.id, runKey: fromRun?.key ?? null });
	}

	async function download(file: WorkspaceArtifact) {
		try {
			await downloadWorkspaceArtifact(workspace.id, file);
			downloadError = null;
		} catch (e) {
			downloadError = failureMessage(e);
		}
	}
</script>

<div class="canvas">
	{#if view.kind === 'file'}
		{#if back}
			<header class="canvas-head">
				<button type="button" class="link back" onclick={() => onNavigate(back.view)}>
					{back.label}
				</button>
			</header>
		{/if}
	{:else if openRun}
		<header class="canvas-head">
			<div class="run-heading">
				<div class="run-name">
					<p class="eyebrow">{RUN_KIND_LABEL[runKind(openRun)]}</p>
					<h2>{runTitle(openRun)}</h2>
				</div>
				<RunStatusIndicator run={openRun} />
			</div>
		</header>
	{/if}

	{#if downloadError}<p class="error">{downloadError}</p>{/if}

	<div class="canvas-body">
		{#if openRun}
			<!-- A run's view starts afresh: nothing chosen for one run carries to the next. -->
			{#key openRun.key}
				<RunDetail
					run={openRun}
					workspaceId={workspace.id}
					initialWindow={view.kind === 'run' ? view.window : undefined}
					onOpenFile={(file) => showFile(file, openRun)}
					onDownload={download}
					{onRenameFile}
				/>
			{/key}
		{:else if openFile}
			{#key openFile.id}
				<ArtifactDetail
					workspaceId={workspace.id}
					file={openFile}
					onDownload={download}
					{onOpenTurn}
					onRename={onRenameFile}
					mapView={mapViewOf(openFile)}
				/>
			{/key}
		{:else if view.kind === 'overview'}
			<p class="empty">
				Nothing here yet. Benchmarks, blends, forecasts and analyses show up here as they are made.
			</p>
		{:else}
			<p class="muted">This result is not in the workspace (yet). It may still be on its way.</p>
		{/if}
	</div>
</div>

<style>
	.canvas {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		flex: 1;
		min-height: 0;
		min-width: 0;
	}
	.canvas-head {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: center;
		gap: 0.4rem 1rem;
		padding-bottom: 0.6rem;
		border-bottom: 1px solid var(--color-border-subtle);
	}
	.run-heading {
		display: flex;
		flex: 1 1 100%;
		align-items: center;
		gap: 0.6rem;
		min-width: 0;
	}
	.run-name {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.eyebrow {
		margin: 0;
		font-size: var(--text-size-caption);
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--color-text-muted);
	}
	h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: 1.05rem;
		overflow-wrap: anywhere;
	}
	.canvas-body {
		display: flex;
		flex-direction: column;
		gap: 0.9rem;
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding-right: 0.25rem;
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: 0.84rem;
		cursor: pointer;
	}
	.muted,
	.empty {
		margin: 0;
		font-size: 0.86rem;
		color: var(--color-text-muted);
	}
	.empty {
		padding: 2rem 1rem;
		text-align: center;
	}
	.error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.84rem;
	}
</style>
