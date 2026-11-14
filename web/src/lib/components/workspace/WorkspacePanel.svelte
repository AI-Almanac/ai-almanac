<script lang="ts">
	import type { FocusAreaValue } from '$lib/api/jobs';
	import type { WorkspaceJob } from '$lib/api/workspaces';
	import { overlay } from '$lib/workspace/overlay';
	import { panelTitle, type OpenPanel } from '$lib/workspace/panels';
	import type { Run } from '$lib/workspace/runs';
	import NewBenchmarkPanel from './NewBenchmarkPanel.svelte';
	import NewBlendPanel from './NewBlendPanel.svelte';
	import NewForecastPanel from './NewForecastPanel.svelte';
	import RunPicker from './RunPicker.svelte';

	interface Props {
		open: OpenPanel;
		/** The workspace's runs, which a setup form starts from. */
		jobs: WorkspaceJob[];
		/** Where the workspace works; a new run with nothing else to start from starts there. */
		area?: FocusAreaValue | null;
		/** Runs were started from a panel; resolves once they are in the workspace. */
		onStarted: (jobIds: string[]) => Promise<void>;
		loadAddableJobs: () => Promise<WorkspaceJob[]>;
		onAddExisting: (run: Run) => Promise<void>;
		onClose: () => void;
	}

	let {
		open,
		jobs,
		area = null,
		onStarted,
		loadAddableJobs,
		onAddExisting,
		onClose
	}: Props = $props();

	const request = $derived(open.request);
</script>

<!-- One named panel over the stage. Opening a panel again, or with another draft, shows it afresh. -->
{#key open.opening}
	<div class="panel" role="dialog" aria-label={panelTitle(request.panel)} use:overlay={{ onClose }}>
		{#if request.panel === 'benchmark_setup'}
			<NewBenchmarkPanel {jobs} {area} draft={request.draft} {onStarted} onCancel={onClose} />
		{:else if request.panel === 'blend_setup'}
			<NewBlendPanel draft={request.draft} {area} {onStarted} onCancel={onClose} />
		{:else if request.panel === 'forecast_setup'}
			<NewForecastPanel {jobs} draft={request.draft} {onStarted} onCancel={onClose} />
		{:else}
			<RunPicker load={loadAddableJobs} onAdd={onAddExisting} {onClose} />
		{/if}
	</div>
{/key}

<style>
	.panel:focus {
		outline: none;
	}
	.panel {
		position: absolute;
		inset: 0;
		z-index: 5;
		display: flex;
		flex-direction: column;
		padding: var(--space-4);
		border-radius: inherit;
		background: var(--color-surface);
		overflow-y: auto;
		overscroll-behavior: contain;
	}
</style>
