<script lang="ts">
	import { untrack } from 'svelte';
	import {
		downloadWorkspaceArtifact,
		failureMessage,
		type WorkspaceArtifact,
		type WorkspaceDetail
	} from '$lib/api';
	import {
		backLink,
		keepView,
		KEPT_VIEWS,
		type CanvasView,
		type KeptView
	} from '$lib/workspace/canvas-view';
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
	import type { Analysis } from '$lib/workspace/analyses';
	import { analysisFileName } from '$lib/workspace/supporting';
	import ArtifactDetail from './ArtifactDetail.svelte';
	import RunDetail from './RunDetail.svelte';
	import RunStatusIndicator from './RunStatusIndicator.svelte';

	interface Props {
		workspace: WorkspaceDetail;
		/** The workspace's runs and analyses, grouped once by the page. */
		results: WorkspaceResults;
		/** Analyses hidden from the list, whose files can still be opened, e.g. from the chat. */
		hiddenAnalyses?: Analysis[];
		view: CanvasView;
		onNavigate: (view: CanvasView) => void;
		onOpenTurn?: (turnId: string) => void;
		onRenameFile?: (file: WorkspaceArtifact, title: string | null) => Promise<void>;
		/** The forecast that can be brought up to date, and how to ask for it; its run offers the update. */
		forecastUpdate?: { jobId: string; request: () => void };
	}

	let {
		workspace,
		results,
		hiddenAnalyses = [],
		view,
		onNavigate,
		onOpenTurn,
		onRenameFile,
		forecastUpdate
	}: Props = $props();

	/** A product the stage can show, keyed so that going back to it finds the same view. */
	type StageTarget =
		| { key: string; kind: 'run'; runKey: string; window?: string }
		| { key: string; kind: 'file'; artifactId: string; fromRunKey: string | null };
	type StageView = StageTarget & KeptView;

	let downloadError = $state<string | null>(null);

	// The stage lands on the newest run until one is chosen.
	const openRun = $derived(
		view.kind === 'run'
			? (results.runs.find((run) => run.key === view.runKey) ?? null)
			: view.kind === 'overview'
				? defaultRun(results.runs)
				: null
	);
	const updatable = $derived(
		openRun?.jobs.some((job) => job.id === forecastUpdate?.jobId) ? forecastUpdate : undefined
	);
	const back = $derived(backLink(view, results.runs.length > 0));
	const openFile = $derived(
		view.kind === 'file'
			? (workspace.artifacts.find((file) => file.id === view.artifactId) ?? null)
			: null
	);

	// A run opened on a lead-time window is its own view: the map starts on that window.
	const target = $derived.by((): StageTarget | null => {
		if (openRun) {
			const window = view.kind === 'run' ? view.window : undefined;
			const key = window ? `run:${openRun.key}@${window}` : `run:${openRun.key}`;
			return { key, kind: 'run', runKey: openRun.key, window };
		}
		if (openFile) {
			const fromRunKey = view.kind === 'file' ? view.runKey : null;
			return { key: `file:${openFile.id}`, kind: 'file', artifactId: openFile.id, fromRunKey };
		}
		return null;
	});
	const targetKey = $derived(target?.key ?? null);

	// Each product keeps its own view while the stage shows others, so going back to one
	// finds its data, map settings and scroll as they were left, with nothing fetched again.
	let kept = $state<StageView[]>([]);
	let uses = 0;
	$effect(() => {
		if (targetKey === null) return;
		untrack(() => {
			if (target) kept = keepView(kept, { ...target, usedAt: ++uses }, KEPT_VIEWS);
		});
	});

	function runNamed(runKey: string | null): Run | null {
		return results.runs.find((run) => run.key === runKey) ?? null;
	}

	function fileNamed(artifactId: string): WorkspaceArtifact | null {
		return workspace.artifacts.find((file) => file.id === artifactId) ?? null;
	}

	/** A file named among the others its analysis made, so a script says what it made. */
	function nameInAnalysis(file: WorkspaceArtifact): string {
		const analysis = [...results.analyses, ...hiddenAnalyses].find((entry) =>
			entry.files.some((f) => f.id === file.id)
		);
		return analysisFileName(file, analysis?.files ?? [file]);
	}

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
				{#if updatable}
					<button type="button" class="update" onclick={updatable.request}>Update forecast</button>
				{/if}
			</div>
		</header>
	{/if}

	{#if downloadError}<p class="error">{downloadError}</p>{/if}

	<div class="canvas-body">
		<!-- One view per product: nothing chosen for one run carries to the next. The views
		     not on show stay in place, unpainted and out of reach. -->
		{#each kept as entry (entry.key)}
			{@const onShow = entry.key === targetKey}
			<div class="kept-view" class:away={!onShow} inert={!onShow}>
				{#if entry.kind === 'run'}
					{@const run = runNamed(entry.runKey)}
					{#if run}
						<RunDetail
							{run}
							workspaceId={workspace.id}
							initialWindow={entry.window}
							onOpenFile={(file) => showFile(file, run)}
							onDownload={download}
							{onRenameFile}
						/>
					{/if}
				{:else}
					{@const file = fileNamed(entry.artifactId)}
					{#if file}
						<ArtifactDetail
							workspaceId={workspace.id}
							{file}
							onDownload={download}
							{onOpenTurn}
							onRename={onRenameFile}
							mapView={mapViewOf(file)}
							displayName={nameInAnalysis(file)}
						/>
					{/if}
				{/if}
			</div>
		{/each}
		{#if !target}
			{#if view.kind === 'overview'}
				<p class="empty">
					Nothing here yet. Benchmarks, blends, forecasts and analyses show up here as they are
					made.
				</p>
			{:else}
				<p class="muted">This result is not in the workspace (yet). It may still be on its way.</p>
			{/if}
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
		position: relative;
		display: flex;
		flex-direction: column;
		flex: 1;
		min-height: 0;
	}
	/* Each kept view scrolls on its own, so its place is still there on the way back. */
	.kept-view {
		position: absolute;
		inset: 0;
		display: flex;
		flex-direction: column;
		gap: 0.9rem;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding-right: 0.25rem;
	}
	/* Kept but not painted: scroll, maps and charts keep their state and size. */
	.kept-view.away {
		content-visibility: hidden;
		pointer-events: none;
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
	.update {
		flex: none;
		margin-left: auto;
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--color-accent);
		border-radius: 0.4rem;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
	}
	.update:hover {
		background: var(--color-surface-muted);
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
