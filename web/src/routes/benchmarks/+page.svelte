<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/stores';
	import LoginPrompt from '$lib/LoginPrompt.svelte';
	import { isAuthenticated } from '$lib/auth-store';
	import { BenchmarkStore } from '$lib/benchmarks.svelte';
	import ResultsViewer from '$lib/components/ResultsViewer.svelte';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import JobLogs from '$lib/components/JobLogs.svelte';
	import { getDatasets, getRegions, type Dataset, type Job, type Region } from '$lib/api';
	import BenchmarkForm from './BenchmarkForm.svelte';
	import BenchmarkSidebar from './BenchmarkSidebar.svelte';

	const store = new BenchmarkStore();

	let regions = $state<Region[]>([]);
	let datasets = $state<Dataset[]>([]);
	let dataLoaded = $state(false);
	let resultsSidebarOpen = $state(true);
	let promptSetupFinished = $state(false);
	let preferredChatSessionId = $state<string | null>(null);
	let initialized = $state(false);
	const initialPrompt = $derived($page.url.searchParams.get('q')?.trim() ?? '');
	const promptSetupActive = $derived(Boolean(initialPrompt) && !promptSetupFinished);
	const inSetupMode = $derived(store.showForm || promptSetupActive);

	async function initializePage() {
		const groupKey = $page.url.searchParams.get('group');
		preferredChatSessionId = $page.url.searchParams.get('chat');
		if (initialPrompt) {
			store.showForm = true;
			store.selectedGroupKey = null;
		}
		await store.load(groupKey, !initialPrompt);
		if (initialPrompt) {
			store.showForm = true;
			store.selectedGroupKey = null;
		}
		const [fetchedRegions, fetchedDatasets] = await Promise.allSettled([
			getRegions(),
			getDatasets()
		]);
		if (fetchedRegions.status === 'fulfilled') regions = fetchedRegions.value;
		if (fetchedDatasets.status === 'fulfilled') datasets = fetchedDatasets.value;
		dataLoaded = true;
	}

	$effect(() => {
		if (!$isAuthenticated || initialized) return;
		initialized = true;
		void initializePage();
	});

	onDestroy(() => store.stopPolling());

	function startNew() {
		promptSetupFinished = true;
		store.showForm = true;
		store.selectedGroupKey = null;
		history.replaceState(null, '', '/benchmarks');
	}

	function selectGroup(key: string) {
		promptSetupFinished = true;
		preferredChatSessionId = null;
		store.selectGroup(key);
		history.replaceState(null, '', `?group=${encodeURIComponent(key)}`);
	}

	function handleSubmitted(groupKey: string, chatSessionId?: string | null) {
		promptSetupFinished = true;
		preferredChatSessionId = chatSessionId ?? null;
		store.showForm = false;
		const params = new URLSearchParams({ group: groupKey });
		if (chatSessionId) params.set('chat', chatSessionId);
		history.replaceState(null, '', `?${params.toString()}`);
	}

	function handleJobsCreated(jobs: Job[]) {
		const runId = jobs[0]?.run_id ?? store.selectedGroupKey;
		if (!runId) return;
		store.acceptSubmittedJobs(runId, jobs);
	}
</script>

{#if !$isAuthenticated}
	<LoginPrompt message="Sign in to view and run benchmarks." />
{:else}
	<div class="page-layout" class:setup-mode={inSetupMode}>
		{#if !inSetupMode}
			<BenchmarkSidebar
				{store}
				onNewBenchmark={startNew}
				onSelectGroup={selectGroup}
			/>
		{/if}
		<div class="main-content">
			{#if inSetupMode}
				<BenchmarkForm
					{store}
					{regions}
					{datasets}
					{dataLoaded}
					{initialPrompt}
					onSubmitted={handleSubmitted}
				/>
			{:else if store.selectedGroup}
				{@const group = store.selectedGroup}
				{@const completeJobs = group.jobs.filter((j) => j.status === 'complete')}
				{@const failedJobs = group.jobs.filter((j) => j.status === 'failed')}

				<div class="analysis-workspace" class:side-collapsed={!resultsSidebarOpen}>
					<section class="analysis-main">
						<header class="analysis-header">
							<div>
								<p class="detail-eyebrow">Analysis run</p>
								<h1 class="detail-title">{group.region}</h1>
								<p class="detail-subtitle">
									{group.jobs.length} model{group.jobs.length !== 1 ? 's' : ''} · {group.eventType}
									{#if group.startDate && group.endDate}
										· {group.startDate} to {group.endDate}
									{/if}
								</p>
							</div>
							<div class="analysis-actions">
								<button
									type="button"
									class="sidebar-toggle"
									aria-expanded={resultsSidebarOpen}
									onclick={() => (resultsSidebarOpen = !resultsSidebarOpen)}
								>
									{resultsSidebarOpen ? 'Hide sidebar' : 'Show assistant'}
								</button>
								<button type="button" class="new-analysis" onclick={startNew}>New analysis</button>
							</div>
						</header>

						<div class="model-status-row">
							{#each group.jobs as job}
								<div
									class="model-pill"
									class:running={job.status === 'running'}
									class:failed={job.status === 'failed'}
									class:complete={job.status === 'complete'}
								>
									<span class="pill-name">{job.model_name.toUpperCase()}</span>
									{#if job.status === 'running'}
										<span class="pill-spinner"></span>
									{:else if job.status === 'failed'}
										<span class="pill-icon fail" title={job.error ?? 'Failed'}>✕</span>
									{:else}
										<span class="pill-icon ok">✓</span>
									{/if}
								</div>
							{/each}
						</div>

						{#if group.jobs.some((j) => j.status === 'running') && completeJobs.length === 0}
							<div class="running-state">
								<div class="spinner"></div>
								<div>
									<strong>Running benchmark</strong>
									<p>Results will appear here as soon as the first model completes.</p>
								</div>
							</div>
						{/if}

						{#if failedJobs.length > 0}
							<div class="failed-runs">
								{#each failedJobs as job}
									<div class="job-error">
										<p class="job-error-title">{job.model_name.toUpperCase()} failed</p>
										{#if job.error}
											<pre class="job-error-msg">{job.error}</pre>
										{/if}
										<JobLogs jobId={job.id} />
									</div>
								{/each}
							</div>
						{/if}

						{#if completeJobs.length > 0}
							<ResultsViewer jobs={completeJobs} />
						{/if}
					</section>

					{#if resultsSidebarOpen}
						<aside class="analysis-side">
							<section class="run-card">
								<div class="side-card-header">
									<p class="detail-eyebrow">Run spec</p>
									<button
										type="button"
										class="icon-toggle"
										aria-label="Collapse sidebar"
										onclick={() => (resultsSidebarOpen = false)}
									>
										×
									</button>
								</div>
								<div class="run-row">
									<span>Region</span>
									<strong>{group.region}</strong>
								</div>
								<div class="run-row">
									<span>Models</span>
									<strong>{group.jobs.map((job) => job.model_name.toUpperCase()).join(', ')}</strong>
								</div>
								{#if group.startDate && group.endDate}
									<div class="run-row">
										<span>Dates</span>
										<strong>{group.startDate} to {group.endDate}</strong>
									</div>
								{/if}
							</section>

							<div class="result-chat">
								<ChatPanel
									jobs={group.jobs}
									scopeKey={group.key}
									preferredSessionId={preferredChatSessionId}
									onJobsCreated={handleJobsCreated}
								/>
							</div>
						</aside>
					{/if}
				</div>
			{:else}
				<div class="empty-state">
					<p class="empty-title">No benchmark runs yet</p>
					<p class="muted">
						Start a new benchmark from the sidebar, or use Ask to describe the run you want.
					</p>
				</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	.page-layout {
		min-height: calc(100vh - 3.5rem);
		width: min(100% - 2rem, 92rem);
		margin: 0 auto;
		padding: 1.25rem 0 2rem;
		display: flex;
		gap: 1.25rem;
		align-items: flex-start;
	}

	.page-layout.setup-mode {
		width: min(100% - 2rem, 76rem);
		min-height: calc(100vh - 4rem);
		padding-top: clamp(1rem, 4vw, 3rem);
		display: block;
	}

	.main-content {
		flex: 1;
		min-width: 0;
	}

	.analysis-workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(22rem, 30rem);
		gap: 1rem;
		align-items: start;
	}

	.analysis-workspace.side-collapsed {
		grid-template-columns: minmax(0, 1fr);
	}

	.analysis-main,
	.run-card,
	.result-chat {
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		box-shadow: var(--shadow-soft);
	}

	.analysis-main {
		padding: clamp(1rem, 2vw, 1.5rem);
		min-width: 0;
	}

	.analysis-side {
		position: sticky;
		top: 5rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
		height: calc(100vh - 6rem);
		min-height: 36rem;
	}

	.analysis-header {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		align-items: flex-start;
		margin-bottom: 1rem;
	}

	.analysis-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
		justify-content: flex-end;
	}

	.new-analysis,
	.sidebar-toggle {
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text);
		padding: 0.55rem 0.75rem;
		font-weight: 750;
		cursor: pointer;
	}

	.sidebar-toggle {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
	}

	.sidebar-toggle:hover,
	.new-analysis:hover {
		border-color: var(--color-accent-border);
		color: var(--color-accent);
	}

	.detail-eyebrow {
		font-size: 0.78rem;
		font-weight: 750;
		letter-spacing: 0.04em;
		color: var(--color-accent);
		margin: 0 0 0.2rem;
		text-transform: uppercase;
	}
	.detail-title {
		font-size: clamp(1.8rem, 4vw, 3.2rem);
		font-weight: 800;
		font-family: var(--font-display);
		margin: 0;
		color: var(--color-text);
		line-height: 1.05;
	}

	.detail-subtitle {
		font-size: 0.9rem;
		color: var(--color-text-muted);
		margin: 0.2rem 0 0;
	}

	/* ---- Model status pills ---- */
	.model-status-row {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin-bottom: 1.25rem;
	}
	.model-pill {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.3rem 0.65rem;
		border-radius: 0.3rem;
		font-size: 0.75rem;
		font-weight: 600;
		font-family: var(--font-mono);
		border: 1px solid var(--color-border-subtle);
		background: var(--color-surface);
	}
	.model-pill.running {
		background: var(--color-status-running-bg);
		border-color: var(--color-status-running);
		color: var(--color-status-running);
	}
	.model-pill.failed {
		background: var(--color-status-failed-bg);
		border-color: var(--color-status-failed);
		color: var(--color-status-failed);
	}
	.model-pill.complete {
		background: var(--color-status-complete-bg);
		border-color: var(--color-status-complete);
		color: var(--color-status-complete);
	}
	.pill-name {
		letter-spacing: 0.05em;
	}
	.pill-icon {
		font-size: 0.7rem;
	}
	.pill-spinner {
		width: 0.6rem;
		height: 0.6rem;
		border: 1.5px solid rgba(251, 191, 36, 0.3);
		border-top-color: var(--color-status-running);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.running-state {
		display: flex;
		align-items: flex-start;
		gap: 0.75rem;
		color: var(--color-text);
		padding: 1rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
		margin-bottom: 1rem;
	}
	.running-state strong,
	.running-state p {
		margin: 0;
	}
	.running-state p {
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.spinner {
		width: 1.1rem;
		height: 1.1rem;
		border: 2px solid var(--color-border-subtle);
		border-top-color: var(--color-accent);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
		flex-shrink: 0;
	}

	.failed-runs {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}

	.run-card {
		padding: 1rem;
	}

	.side-card-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.icon-toggle {
		border: 0;
		border-radius: 0.3rem;
		background: transparent;
		color: var(--color-text-muted);
		font: inherit;
		font-size: 1rem;
		line-height: 1;
		cursor: pointer;
		padding: 0.2rem 0.35rem;
	}

	.icon-toggle:hover {
		background: var(--color-surface-muted);
		color: var(--color-text);
	}

	.run-row {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.6rem 0;
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.run-row:last-child {
		border-bottom: 0;
	}

	.run-row span {
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}

	.run-row strong {
		max-width: 60%;
		text-align: right;
		font-size: 0.85rem;
		font-weight: 650;
	}

	.result-chat {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-height: 0;
		overflow: hidden;
	}

	.result-chat > :global(.chat-panel) {
		border: 0;
		border-radius: 0;
		box-shadow: none;
	}
	.job-error {
		border: 1px solid var(--color-danger-border);
		border-radius: 6px;
		padding: 0.75rem 1rem;
		background: var(--color-danger-bg);
	}
	.job-error-title {
		margin: 0 0 0.35rem;
		font-size: 0.75rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		color: var(--color-danger);
	}
	.job-error-msg {
		margin: 0 0 0.5rem;
		font-size: 0.78rem;
		color: var(--color-text-muted);
		white-space: pre-wrap;
		word-break: break-word;
		font-family: var(--font-mono);
	}

	/* ---- Empty state ---- */
	.empty-state {
		padding: clamp(2rem, 8vw, 5rem);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
	}
	.empty-title {
		font-size: 0.95rem;
		font-weight: 600;
		color: var(--color-text-muted);
		margin: 0 0 0.5rem;
	}
	.muted {
		color: var(--color-text-dim);
		font-size: 0.9rem;
		margin: 0;
	}

	@media (max-width: 1050px) {
		.analysis-workspace {
			grid-template-columns: 1fr;
		}

		.analysis-header {
			flex-direction: column;
		}

		.analysis-actions {
			justify-content: flex-start;
		}

		.analysis-side {
			position: static;
			height: auto;
			min-height: 0;
		}

		.result-chat {
			min-height: 34rem;
		}
	}
</style>
