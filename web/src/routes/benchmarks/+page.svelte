<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/stores';
	import LoginPrompt from '$lib/LoginPrompt.svelte';
	import { isAuthenticated } from '$lib/auth-store';
	import { BenchmarkStore } from '$lib/benchmarks.svelte';
	import ResultsViewer from '$lib/components/ResultsViewer.svelte';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import JobLogs from '$lib/components/JobLogs.svelte';
	import {
		getDatasets,
		getRegions,
		getRompDefaults,
		type Dataset,
		type Job,
		type Region,
		type RompDefaults
	} from '$lib/api';
	import { EVENT_TYPES } from '$lib/data/event-types';
	import BenchmarkForm from './BenchmarkForm.svelte';
	import BenchmarkSidebar from './BenchmarkSidebar.svelte';

	const store = new BenchmarkStore();

	let regions = $state<Region[]>([]);
	let datasets = $state<Dataset[]>([]);
	let parameterDefaults = $state<RompDefaults | null>(null);
	let dataLoaded = $state(false);
	let resultsSidebarOpen = $state(true);
	let promptSetupFinished = $state(false);
	let preferredChatSessionId = $state<string | null>(null);
	let initialized = $state(false);
	const initialPrompt = $derived($page.url.searchParams.get('q')?.trim() ?? '');
	const manualSetupRequested = $derived($page.url.searchParams.get('manual') === '1');
	const promptSetupActive = $derived(Boolean(initialPrompt) && !promptSetupFinished);
	const inSetupMode = $derived(store.showForm || promptSetupActive || manualSetupRequested);

	async function initializePage() {
		const groupKey = $page.url.searchParams.get('group');
		preferredChatSessionId = $page.url.searchParams.get('chat');
		if (initialPrompt || manualSetupRequested) {
			store.showForm = true;
			store.selectedGroupKey = null;
		}
		await store.load(groupKey, !initialPrompt && !manualSetupRequested);
		if (initialPrompt || manualSetupRequested) {
			store.showForm = true;
			store.selectedGroupKey = null;
		}
		const [fetchedRegions, fetchedDatasets, fetchedParameterDefaults] = await Promise.allSettled([
			getRegions(),
			getDatasets(),
			getRompDefaults()
		]);
		if (fetchedRegions.status === 'fulfilled') regions = fetchedRegions.value;
		if (fetchedDatasets.status === 'fulfilled') datasets = fetchedDatasets.value;
		if (fetchedParameterDefaults.status === 'fulfilled') {
			parameterDefaults = fetchedParameterDefaults.value;
		}
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

	function modelDisplayName(modelName: string): string {
		const labels: Record<string, string> = {
			fuxi: 'FuXi',
			aifs: 'AIFS',
			aifs_daily: 'AIFS Daily',
			fuxi_s2s: 'FuXi S2S',
			climatology: 'Climatology'
		};
		return labels[modelName.toLowerCase()] ?? modelName;
	}

	function eventTypeName(eventType: string): string {
		return EVENT_TYPES.find((event) => event.id === eventType)?.name ?? eventType;
	}

	function formatRunDate(value: string): string {
		if (!value) return 'Unknown';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat(undefined, {
			year: 'numeric',
			month: 'short',
			day: 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		}).format(date);
	}

	function formatRunStatus(jobs: Job[]): string {
		if (jobs.some((job) => job.status === 'running')) return 'Running';
		if (jobs.every((job) => job.status === 'complete')) return 'Complete';
		if (jobs.every((job) => job.status === 'failed')) return 'Failed';
		return 'Mixed';
	}

	function compactDateRange(startDate?: string, endDate?: string): string {
		if (!startDate || !endDate) return 'Unknown';
		return `${startDate} to ${endDate}`;
	}

	function forecastWindow(params: Job['params']): string {
		return params?.max_forecast_day ? `Days 1-${params.max_forecast_day}` : 'All available days';
	}

	function climatePeriod(params: Job['params']): string {
		if (!params?.start_year_clim || !params?.end_year_clim) return 'Default climatology period';
		return `${params.start_year_clim} to ${params.end_year_clim}`;
	}

	function initDays(params: Job['params']): string {
		return params?.init_days?.trim() || 'Default initialization days';
	}

	function parameterValue(
		value: number | string | undefined,
		fallback: number | string | undefined,
		unit?: string
	): string {
		const resolved = value ?? fallback;
		if (resolved === undefined || resolved === '') return 'Not recorded';
		return unit ? `${resolved} ${unit}` : String(resolved);
	}
</script>

{#if !$isAuthenticated}
	<LoginPrompt message="Sign in to view and run benchmarks." />
{:else}
	<div class="page-layout" class:setup-mode={inSetupMode}>
		{#if !inSetupMode}
			<BenchmarkSidebar {store} onNewBenchmark={startNew} onSelectGroup={selectGroup} />
		{/if}
		<div class="main-content">
			{#if inSetupMode}
				<BenchmarkForm
					{store}
					{regions}
					{datasets}
					{dataLoaded}
					{initialPrompt}
					initialManualOpen={manualSetupRequested}
					onSubmitted={handleSubmitted}
				/>
			{:else if store.selectedGroup}
				{@const group = store.selectedGroup}
				{@const completeJobs = group.jobs.filter((j) => j.status === 'complete')}
				{@const failedJobs = group.jobs.filter((j) => j.status === 'failed')}
				{@const primaryJob = group.jobs[0]}

				<div class="analysis-workspace" class:side-collapsed={!resultsSidebarOpen}>
					<section class="analysis-main">
						<header class="analysis-header">
							<div>
								<p class="detail-eyebrow">Analysis run</p>
								<h1 class="detail-title">{group.region}</h1>
								<p class="detail-subtitle">
									{group.jobs.length} model{group.jobs.length !== 1 ? 's' : ''} · {eventTypeName(
										group.eventType
									)}
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

						<details class="benchmark-summary">
							<summary class="summary-trigger">
								<span class="summary-trigger-title">Benchmark summary</span>
								<span class="summary-trigger-meta">
									{formatRunStatus(group.jobs)} · {group.jobs.length} model{group.jobs.length === 1
										? ''
										: 's'} · {forecastWindow(primaryJob?.params)} · Run {formatRunDate(
										group.mostRecentAt
									)}
								</span>
							</summary>

							<div class="run-summary-grid">
								<div class="run-fact">
									<span>Run date</span>
									<strong>{formatRunDate(group.mostRecentAt)}</strong>
								</div>
								<div class="run-fact">
									<span>Status</span>
									<strong>{formatRunStatus(group.jobs)}</strong>
								</div>
								<div class="run-fact">
									<span>Region</span>
									<strong>{group.region}</strong>
								</div>
								<div class="run-fact">
									<span>Event type</span>
									<strong>{eventTypeName(group.eventType)}</strong>
								</div>
							</div>

							<div class="summary-detail-grid">
								<div class="run-section">
									<h3>Models Run</h3>
									<div class="model-run-list">
										{#each group.jobs as job}
											<div class="model-run-item">
												<strong>{modelDisplayName(job.model_name)}</strong>
												<span
													class:complete={job.status === 'complete'}
													class:failed={job.status === 'failed'}
													class:running={job.status === 'running'}
												>
													{job.status}
												</span>
											</div>
										{/each}
									</div>
								</div>

								<div class="run-section">
									<h3>Benchmark Configuration</h3>
									<div class="run-row">
										<span>Forecast period</span>
										<strong>{compactDateRange(group.startDate, group.endDate)}</strong>
									</div>
									<div class="run-row">
										<span>Forecast window</span>
										<strong>{forecastWindow(primaryJob?.params)}</strong>
									</div>
									<div class="run-row">
										<span>Climatology period</span>
										<strong>{climatePeriod(primaryJob?.params)}</strong>
									</div>
									<div class="run-row">
										<span>Initialization days</span>
										<strong>{initDays(primaryJob?.params)}</strong>
									</div>
								</div>
							</div>

							<div class="run-section">
								<h3>Parameters</h3>
								<div class="parameter-grid">
									<div>
										<span>Wet threshold</span>
										<strong
											>{parameterValue(
												primaryJob?.params?.wet_threshold,
												parameterDefaults?.wet_threshold,
												'millimeters'
											)}</strong
										>
									</div>
									<div>
										<span>Wet initialization</span>
										<strong
											>{parameterValue(
												primaryJob?.params?.wet_init,
												parameterDefaults?.wet_init,
												'millimeters'
											)}</strong
										>
									</div>
									<div>
										<span>Wet spell</span>
										<strong
											>{parameterValue(
												primaryJob?.params?.wet_spell,
												parameterDefaults?.wet_spell,
												'days'
											)}</strong
										>
									</div>
									<div>
										<span>Dry spell</span>
										<strong
											>{parameterValue(
												primaryJob?.params?.dry_spell,
												parameterDefaults?.dry_spell,
												'days'
											)}</strong
										>
									</div>
									<div>
										<span>Dry extent</span>
										<strong
											>{parameterValue(
												primaryJob?.params?.dry_extent,
												parameterDefaults?.dry_extent,
												'days'
											)}</strong
										>
									</div>
									<div>
										<span>Observation variable</span>
										<strong
											>{parameterValue(
												primaryJob?.params?.obs_var,
												parameterDefaults?.obs_var
											)}</strong
										>
									</div>
								</div>
							</div>
						</details>

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
										<p class="job-error-title">{modelDisplayName(job.model_name)} failed</p>
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
	.benchmark-summary,
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

	.benchmark-summary {
		margin-bottom: 1rem;
		padding: 0;
		overflow: hidden;
	}

	.summary-trigger {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.7rem 0.85rem;
		cursor: pointer;
		list-style: none;
		user-select: none;
	}

	.summary-trigger::-webkit-details-marker {
		display: none;
	}

	.summary-trigger::before {
		content: '▸';
		color: var(--color-text);
		font-size: 0.7rem;
		transition: transform 0.15s;
	}

	.benchmark-summary[open] .summary-trigger::before {
		transform: rotate(90deg);
	}

	.summary-trigger-title {
		color: var(--color-text);
		font-size: 0.82rem;
		font-weight: 850;
		letter-spacing: 0.02em;
		text-transform: uppercase;
		white-space: nowrap;
	}

	.summary-trigger-meta {
		min-width: 0;
		flex: 1;
		color: var(--color-text-muted);
		font-size: 0.82rem;
		overflow: hidden;
		text-align: right;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.benchmark-summary[open] {
		padding-bottom: 0.9rem;
	}

	.run-summary-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr));
		gap: 0.6rem;
		padding: 0 0.85rem;
	}

	.run-fact {
		padding: 0.65rem 0.7rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.45rem;
		background: var(--color-bg);
	}

	.run-fact span,
	.parameter-grid span {
		display: block;
		margin-bottom: 0.25rem;
		color: var(--color-text-muted);
		font-size: 0.72rem;
		font-weight: 750;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.run-fact strong {
		display: block;
		color: var(--color-text);
		font-size: 0.88rem;
		line-height: 1.25;
	}

	.summary-detail-grid {
		display: grid;
		grid-template-columns: minmax(12rem, 0.8fr) minmax(16rem, 1.2fr);
		gap: 1rem;
		border-top: 1px solid var(--color-border-subtle);
		margin: 0.85rem 0.85rem 0;
		padding-top: 0.85rem;
	}

	.run-section {
		min-width: 0;
	}

	.benchmark-summary > .run-section {
		border-top: 1px solid var(--color-border-subtle);
		margin: 0.85rem 0.85rem 0;
		padding-top: 0.85rem;
	}

	.run-section h3 {
		margin: 0 0 0.65rem;
		color: var(--color-text);
		font-size: 0.82rem;
		font-weight: 850;
		letter-spacing: 0.02em;
		text-transform: uppercase;
	}

	.model-run-list {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
	}

	.model-run-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.5rem 0.6rem;
		border-radius: 0.45rem;
		background: var(--color-bg);
	}

	.model-run-item strong {
		color: var(--color-text);
	}

	.model-run-item span {
		border-radius: 999rem;
		padding: 0.18rem 0.45rem;
		font-size: 0.68rem;
		font-weight: 800;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-text-muted);
		background: var(--color-surface-muted);
	}

	.model-run-item span.complete {
		background: var(--color-status-complete-bg);
		color: var(--color-status-complete);
	}

	.model-run-item span.failed {
		background: var(--color-status-failed-bg);
		color: var(--color-status-failed);
	}

	.model-run-item span.running {
		background: var(--color-status-running-bg);
		color: var(--color-status-running);
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

	.parameter-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 9rem), 1fr));
		gap: 0.5rem;
	}

	.parameter-grid div {
		min-width: 0;
		padding: 0.55rem 0.6rem;
		border-radius: 0.45rem;
		background: var(--color-bg);
	}

	.parameter-grid strong {
		display: block;
		color: var(--color-text);
		font-size: 0.85rem;
		line-height: 1.25;
		overflow-wrap: anywhere;
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

		.summary-detail-grid {
			grid-template-columns: 1fr;
		}

		.summary-trigger {
			align-items: flex-start;
			flex-wrap: wrap;
		}

		.summary-trigger-meta {
			flex-basis: 100%;
			padding-left: 1.3rem;
			text-align: left;
		}

		.result-chat {
			min-height: 34rem;
		}
	}
</style>
