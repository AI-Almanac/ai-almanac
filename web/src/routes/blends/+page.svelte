<script lang="ts">
	import { badgeLabel, badgeTone, isActiveStatus, runTime, sidebarStatus } from '$lib/run-status';
	import { downloadJobArtifact } from '$lib/api/jobs';
	import { failureMessage } from '$lib/api/core';
	import { pollWhileActive } from '$lib/poll';
	import { page } from '$app/stores';
	import { account } from '$lib/account.svelte';
	import LoginPrompt from '$lib/LoginPrompt.svelte';
	import {
		listBlends,
		listDataSources,
		getCapabilities,
		getGuardrailThresholds,
		getJobArtifacts,
		cancelJob,
		deleteJob,
		type Blend,
		type BlendRunSpec,
		type ChatScope,
		type DataSource,
		type Job,
		type JobArtifact,
		type JobStatus
	} from '$lib/api';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import OpenInWorkspace from '$lib/components/OpenInWorkspace.svelte';
	import ExampleActions from '$lib/components/ExampleActions.svelte';
	import SplitResizer from '$lib/components/SplitResizer.svelte';
	import RunSidebar, { type RunSection } from '$lib/components/RunSidebar.svelte';
	import BlendForm from '$lib/components/blend-setup/BlendForm.svelte';
	import { DEFAULT_MIN_TRAINING_YEARS } from '$lib/components/blend-setup/year-coverage';
	import BlendResults from '$lib/components/blend/BlendResults.svelte';
	import BlendOutputs from './BlendOutputs.svelte';
	import { awaitingArtifacts, blendInProgress, blendStateChanged, dayLevelNote } from './day-level';
	import { installTour } from '$lib/tour.svelte';
	import { takeSetupRequest } from '$lib/setup-request';
	import { blendResultsSteps, blendSetupSteps } from './tours';

	let blends = $state<Blend[]>([]);
	let obsSources = $state<DataSource[]>([]);
	// Each model source carries the server's live-forecast verdict
	// (source.live_forecast): whether a registry model exists for it and runs
	// on the archive's grid — the same check create_forecast_for_user enforces.
	let modelSources = $state<DataSource[]>([]);
	let selectedId = $state<string | null>(null);
	let creating = $state(false);
	let loaded = $state(false);
	let chatAvailable = $state(false);
	// Whether new blends train the day-level blend, so its settings are offered.
	let dayLevelAvailable = $state(false);
	// A comparison needs a wider assistant column than the normal rail.
	let chatComparing = $state(false);
	let splitEl = $state<HTMLElement | null>(null);
	const blendSetupKey = crypto.randomUUID();
	const initialPrompt = $page.url.searchParams.get('q')?.trim() ?? '';
	// The new flag is dropped from the URL on load, so sign-in is sent back with it.
	const setupReturnTo = initialPrompt ? $page.url.pathname + $page.url.search : '/blends?new=1';

	// A chat session that follows the user into a blend: either started here in
	// setup, or carried over from the benchmark flow that launched the blend.
	// Keeping the originating scope lets follow-up messages match the session.
	let continuedSessionId = $state<string | null>(null);
	let continuedScope = $state<{ kind: ChatScope['kind']; key: string } | null>(null);
	let continuedBlendId = $state<string | null>(null);
	let initialized = $state(false);

	const SCOPE_KINDS: ChatScope['kind'][] = [
		'benchmark_setup',
		'benchmark_run_group',
		'blend_setup',
		'job_set'
	];
	function asScopeKind(value: string | null): ChatScope['kind'] | null {
		return value && SCOPE_KINDS.includes(value as ChatScope['kind'])
			? (value as ChatScope['kind'])
			: null;
	}

	$effect(() => {
		if (initialized) return;
		initialized = true;
		const params = $page.url.searchParams;
		const blendId = params.get('blend');
		const chatId = params.get('chat');
		const scopeKind = asScopeKind(params.get('scopeKind'));
		const scopeKey = params.get('scopeKey');
		if (blendId) selectedId = blendId;
		else if (takeSetupRequest('new') || initialPrompt) startNew();
		if (chatId && scopeKind && scopeKey) {
			continuedSessionId = chatId;
			continuedScope = { kind: scopeKind, key: scopeKey };
			continuedBlendId = blendId;
		}
	});

	let artifacts = $state<JobArtifact[]>([]);
	// The blend `artifacts` belongs to; plain, so reading it tracks nothing.
	let artifactsFor: string | null = null;

	const selected = $derived(blends.find((b) => b.id === selectedId) ?? null);
	installTour(
		'blend',
		() => blendResultsSteps(startNew),
		() => !creating && selected?.status === 'complete'
	);
	installTour('blend-setup', blendSetupSteps, () => creating);
	const hasActive = $derived(blends.some((b) => isActiveStatus(b.status)));

	// The chat panel beside a selected blend. A session carried over from the
	// benchmark flow keeps its original scope for continuity; any other blend gets
	// a stable blend-scoped session so the assistant can read its results.
	const detailChat = $derived.by(() => {
		if (!selected) return null;
		if (continuedSessionId && continuedScope && selected.id === continuedBlendId) {
			return {
				scopeKind: continuedScope.kind,
				scopeKey: continuedScope.key,
				sessionId: continuedSessionId
			};
		}
		return { scopeKind: 'blend_setup' as const, scopeKey: selected.id, sessionId: null };
	});
	// ChatPanel scopes on job ids; the blend is itself a job, so hand it through so
	// the assistant can call get_blend_results on it.
	const detailChatJobs = $derived(selected ? [{ id: selected.id } as Job] : []);
	let detailChatSessionId = $state<string | null>(null);

	// The enforced minimum, so the prefilled split is one the server accepts.
	let minTrainingYears = $state(DEFAULT_MIN_TRAINING_YEARS);
	let draftSetup = $state.raw<BlendRunSpec | null>(null);

	async function load() {
		const [b, obs, models, caps, thresholds] = await Promise.allSettled([
			listBlends(),
			listDataSources('obs'),
			listDataSources('model'),
			getCapabilities(),
			getGuardrailThresholds()
		]);
		if (b.status === 'fulfilled') blends = b.value;
		if (!selectedId && !creating) selectedId = defaultBlend(blends)?.id ?? null;
		if (obs.status === 'fulfilled') obsSources = obs.value.filter((s) => s.status === 'ready');
		if (models.status === 'fulfilled')
			modelSources = models.value.filter((s) => s.status === 'ready');
		if (caps.status === 'fulfilled') {
			chatAvailable = caps.value.chat;
			dayLevelAvailable = caps.value.day_level_blend ?? false;
		}
		if (thresholds.status === 'fulfilled') minTrainingYears = thresholds.value.min_training_years;
		loaded = true;
	}

	// The assistant's blend setup, kept here so it fills the form whether or not the
	// form is on screen when it arrives.
	function applyBlendConfig(config: BlendRunSpec) {
		draftSetup = config;
	}

	function handleBlendSubmitted(_runId: string, jobs: Blend[], sessionId: string | null) {
		if (jobs.length === 0) return;
		blends = [...jobs, ...blends];
		selectedId = jobs[0].id;
		creating = false;
		continuedSessionId = sessionId;
		continuedScope = { kind: 'blend_setup', key: blendSetupKey };
		continuedBlendId = jobs[0].id;
	}

	let actionError = $state<string | null>(null);

	async function cancelBlend(id: string) {
		actionError = null;
		try {
			const updated = await cancelJob(id);
			patchBlendStatus(id, updated.status);
		} catch (err) {
			actionError = failureMessage(err);
		}
	}

	async function deleteBlend(id: string) {
		const blend = blends.find((b) => b.id === id);
		if (!blend) return;
		const label = blend.name || 'this blend';
		const message = isExample(blend)
			? `Remove "${label}" from your list? This example stays available to everyone else.`
			: `Delete "${label}"? This permanently removes its trained weights and outputs from storage and cannot be undone.`;
		if (!confirm(message)) {
			return;
		}
		actionError = null;
		try {
			await deleteJob(id);
			blends = blends.filter((b) => b.id !== id);
			if (selectedId === id) selectedId = null;
			if (continuedBlendId === id) {
				continuedSessionId = null;
				continuedScope = null;
				continuedBlendId = null;
			}
		} catch (err) {
			actionError = failureMessage(err);
		}
	}

	function isExample(b: (typeof blends)[number]): boolean {
		// Ownership doesn't matter: the server hides (never deletes) an
		// example for every caller, owner and admin included.
		return b.visibility === 'example';
	}

	/** The user's newest blend, or an example when they have none of their own. */
	function defaultBlend(all: Blend[]): Blend | undefined {
		const newestFirst = (a: Blend, b: Blend) => b.created_at.localeCompare(a.created_at);
		return (
			all.filter((b) => !isExample(b)).sort(newestFirst)[0] ??
			all.filter(isExample).sort(newestFirst)[0]
		);
	}

	function toSidebarItem(b: (typeof blends)[number]) {
		return {
			id: b.id,
			title: b.name || 'Untitled blend',
			meta: `${b.model_names.length} model${b.model_names.length === 1 ? '' : 's'} · ${runTime(b.created_at)}`,
			count: b.model_names.length,
			status: sidebarStatus(b.status),
			canDelete: !isActiveStatus(b.status),
			// Deleting a non-owned example only hides it from this account.
			deleteTitle: isExample(b) ? 'Remove example' : undefined
		};
	}

	const sidebarSections = $derived.by<RunSection[]>(() => {
		const mine = blends.filter((b) => !isExample(b));
		const examples = blends.filter(isExample);
		const result: RunSection[] = [
			{
				title: 'My Blends',
				open: true,
				emptyLabel: loaded ? 'No blends yet.' : 'Loading…',
				items: mine.map(toSidebarItem)
			}
		];
		if (examples.length > 0) {
			result.push({
				title: 'Examples',
				items: examples.map(toSidebarItem),
				open: mine.length === 0
			});
		}
		return result;
	});

	// Stream status changes per running blend instead of re-fetching the whole
	// list on a timer. Replacing the array on every poll reassigned every blend
	// object, which remounted the detail view and made the page jump. Here only
	// the blend whose status actually changed is rewritten.
	// While any blend is active, re-fetch the list and replace only the blends
	// whose status changed (a wholesale rewrite remounts the detail view and
	// makes the page jump). Bounded by the server's ~5s reconcile cadence, so a
	// 3s poll is as live as the data gets. Returning the stop fn lets $effect
	// tear the timer down on unmount.
	// A complete blend keeps being polled while its day-level blend trains.
	$effect(() => pollWhileActive(() => blends.some((b) => blendInProgress(b)), syncBlendStatuses));

	async function syncBlendStatuses() {
		let fresh: Blend[];
		try {
			fresh = await listBlends();
		} catch {
			return; // transient — next tick retries
		}
		const byId = new Map(fresh.map((b) => [b.id, b]));
		blends = blends.map((b) => {
			const updated = byId.get(b.id);
			return updated && blendStateChanged(b, updated) ? updated : b;
		});
	}

	function patchBlendStatus(id: string, status: JobStatus) {
		const idx = blends.findIndex((b) => b.id === id);
		if (idx === -1 || blends[idx].status === status) return;
		blends[idx] = { ...blends[idx], status };
	}

	$effect(() => {
		if (!loaded) void load();
	});

	// Load a completed blend's artifacts. Publication is a background step that
	// lags completion by a few seconds, so a list still missing files is retried
	// until they land rather than cached as final. A day-level blend finishing
	// later reloads the list with its files.
	$effect(() => {
		const job = selected;
		if (job?.status !== 'complete') return;
		const id = job.id;
		const state = { status: job.status, day_level_status: job.day_level_status };
		// Another blend's files never show; this blend's stay up while they reload.
		if (artifactsFor !== id) {
			artifacts = [];
			artifactsFor = id;
		}
		let cancelled = false;
		let attempts = 0;
		const load = async () => {
			if (cancelled) return;
			try {
				const found = await getJobArtifacts(id);
				if (cancelled) return;
				artifacts = found;
				if (awaitingArtifacts(state, found) && attempts++ < 6) setTimeout(load, 2000);
			} catch {
				if (!cancelled) artifacts = [];
			}
		};
		void load();
		return () => {
			cancelled = true;
		};
	});

	const summaryReady = $derived(
		artifacts.some((a) => a.filename.startsWith('summary_models_pooled'))
	);
	// Changes once a day-level blend that trained apart has published its files,
	// so the skill summary and map reload with its results.
	const resultsRevision = $derived(
		selected && !awaitingArtifacts(selected, artifacts) ? (selected.day_level_status ?? '') : ''
	);

	function startNew() {
		creating = true;
		selectedId = null;
	}

	function selectBlend(id: string) {
		selectedId = id;
		creating = false;
	}

	function showNewBlend(blend: Blend) {
		blends = [blend, ...blends];
		creating = false;
		selectedId = blend.id;
		draftSetup = null;
	}
</script>

<svelte:head><title>Blends · AI Almanac</title></svelte:head>

<div class="workspace-page" class:is-setup={creating}>
	{#if !creating}
		<RunSidebar
			newLabel="New blend"
			{selectedId}
			sections={sidebarSections}
			onNew={startNew}
			onSelect={selectBlend}
			onDelete={deleteBlend}
			deleteTitle="Delete blend"
		/>
	{/if}

	<div class="workspace-main">
		{#if creating && account.isAnonymous}
			<LoginPrompt
				message="Sign in to train a blend. The example blends are open to everyone."
				returnTo={setupReturnTo}
			>
				<button type="button" class="secondary-action" onclick={() => (creating = false)}>
					See the example blends
				</button>
			</LoginPrompt>
		{:else if creating}
			<div class="setup-layout" class:with-chat={chatAvailable} class:is-comparing={chatComparing}>
				{#if chatAvailable}
					<div class="setup-chat" data-tour="setup-chat">
						<ChatPanel
							jobs={[]}
							scopeKind="blend_setup"
							scopeKey={blendSetupKey}
							title="Blend setup"
							initialMessage={initialPrompt}
							emptyMessage="Describe the blend you want, or ask which models to combine based on your benchmark results."
							placeholder="Ask for the blend you want, or a question…"
							suggestions={[
								'Set up a blend for Kiremt onset in Ethiopia',
								'Which models should I combine based on my benchmarks?',
								'What do the training and cross-validation years mean?'
							]}
							onBlendConfig={applyBlendConfig}
							onBlendSubmitted={handleBlendSubmitted}
						/>
					</div>
				{/if}
				<section class="card setup-form">
					<BlendForm
						{obsSources}
						{modelSources}
						{dayLevelAvailable}
						{minTrainingYears}
						startFrom={draftSetup}
						onSubmitted={showNewBlend}
						onCancel={() => (creating = false)}
					>
						{#snippet heading()}
							<h1>Train a blend</h1>
							<p class="muted">
								Combine multiple forecast models into a single blended forecast. Training learns the
								weights and saves them as a downloadable artifact.
							</p>
						{/snippet}
					</BlendForm>
				</section>
			</div>
		{:else if selected}
			<div
				class="workspace-split"
				class:is-solo={!(chatAvailable && detailChat)}
				class:is-comparing={chatComparing}
				bind:this={splitEl}
			>
				<section class="card detail">
					<header class="detail-header">
						<div class="detail-id">
							<p class="eyebrow">Blend</p>
							<h1 class="detail-title" title={selected.name || 'Untitled blend'}>
								{selected.name || 'Untitled blend'}
							</h1>
							<p class="detail-meta">
								<span>{selected.model_names.join(', ')}</span>
								{#if selected.region_id}
									<span class="dot" aria-hidden="true">·</span>
									<span>{selected.region_id}</span>
								{/if}
								<span class="dot" aria-hidden="true">·</span>
								<span>Submitted {runTime(selected.created_at)}</span>
								{#if selected.completed_at}
									<span class="dot" aria-hidden="true">·</span>
									<span>Completed {runTime(selected.completed_at)}</span>
								{/if}
							</p>
						</div>
						<div class="detail-actions">
							<ExampleActions
								promoteId={selected.status === 'complete' ? selected.id : null}
								demoteIds={[selected.id]}
								isExample={selected.visibility === 'example'}
								onChanged={load}
							/>
							<span class="status-badge {badgeTone(selected.status)}"
								>{badgeLabel(selected.status)}</span
							>
							{#if isActiveStatus(selected.status)}
								<button
									type="button"
									class="ghost"
									disabled={selected.status === 'canceling'}
									onclick={() => cancelBlend(selected!.id)}
								>
									{selected.status === 'canceling' ? 'Canceling…' : 'Cancel'}
								</button>
							{/if}
						</div>
					</header>

					{#if actionError}
						<p class="error">{actionError}</p>
					{/if}

					{#if isActiveStatus(selected.status)}
						<div class="running-state">
							<div class="spinner"></div>
							<div>
								<strong>Training blend</strong>
								<p class="muted">Weights will appear here when training completes.</p>
							</div>
						</div>
					{/if}

					{#if selected.status === 'failed' && selected.error}
						<pre class="error-block">{selected.error}</pre>
					{/if}

					{#if selected.status === 'complete'}
						{@const dayLevel = dayLevelNote(selected)}
						{#if dayLevel}
							<div class="day-level-note {dayLevel.kind}" role="status">
								<div class="day-level-text">
									<strong>
										{#if dayLevel.kind === 'training'}
											<span class="training-dot" aria-hidden="true"></span>
										{/if}
										{dayLevel.title}
									</strong>
									{#if dayLevel.kind === 'training'}
										<p class="muted">{dayLevel.detail}</p>
									{:else if dayLevel.detail}
										<details>
											<summary>Details</summary>
											<pre class="error-block">{dayLevel.detail}</pre>
										</details>
									{/if}
								</div>
							</div>
						{/if}
						<BlendResults jobId={selected.id} {summaryReady} revision={resultsRevision} />

						<div class="artifacts" data-tour="blend-outputs">
							<h2>Results files</h2>
							{#if artifacts.length === 0}
								<p class="muted">No files found.</p>
							{:else}
								<BlendOutputs
									jobId={selected.id}
									{artifacts}
									trueHoldoutYears={selected.true_holdout_years ?? []}
									ondownload={downloadJobArtifact}
								/>
							{/if}
						</div>
					{/if}
				</section>
				{#if chatAvailable && detailChat}
					<SplitResizer container={splitEl} comparing={chatComparing} storageKey="blend-detail" />
					<aside class="workspace-aside">
						<OpenInWorkspace sessionId={detailChatSessionId} />
						<div class="result-chat" data-tour="blend-chat">
							<ChatPanel
								jobs={detailChatJobs}
								scopeKind={detailChat.scopeKind}
								scopeKey={detailChat.scopeKey}
								preferredSessionId={detailChat.sessionId}
								onSessionReady={(sessionId) => (detailChatSessionId = sessionId)}
								title={selected.name || 'Blend'}
								emptyMessage="Ask about this blend or its training results."
								placeholder="Ask about this blend…"
								suggestions={[
									'How does the blend compare to the individual models?',
									'Summarise the forecast skill of this blend.'
								]}
								onComparingChange={(value) => (chatComparing = value)}
								onBlendConfig={applyBlendConfig}
								onBlendSubmitted={handleBlendSubmitted}
							/>
						</div>
					</aside>
				{/if}
			</div>
		{:else}
			<div class="card empty-state">
				<p class="empty-title">No blend selected</p>
				<p class="muted">
					Pick a blend from the list, or start a new one to train blending weights.
				</p>
			</div>
		{/if}
	</div>
</div>

<style>
	.setup-layout {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}

	/* Sticky assistant column, matching the benchmarks analysis view. */
	.result-chat {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-height: 0;
		overflow: hidden;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		box-shadow: var(--shadow-soft);
	}

	.result-chat > :global(.chat-panel) {
		border: 0;
		border-radius: 0;
		box-shadow: none;
	}

	.setup-layout.with-chat {
		flex-direction: row;
		align-items: stretch;
	}

	.setup-chat {
		flex: 1 1 50%;
		min-width: 0;
		display: flex;
		position: sticky;
		top: var(--workspace-pinned-top);
		height: var(--workspace-screen-height);
	}

	.setup-chat :global(.chat-panel) {
		width: 100%;
		min-height: 100%;
		box-shadow: var(--shadow-soft);
	}

	.setup-layout.with-chat.is-comparing .setup-chat {
		flex: 1 1 66%;
	}

	.setup-layout.with-chat.is-comparing .setup-form {
		flex: 1 1 34%;
	}

	.setup-layout.with-chat .setup-form {
		flex: 1 1 50%;
		min-width: 0;
		align-self: flex-start;
	}

	@media (max-width: 60rem) {
		.setup-layout.with-chat {
			flex-direction: column;
		}

		/* Stacked above the form, a pinned chat would cover it while scrolling. */
		.setup-chat {
			position: static;
		}
	}

	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		box-shadow: var(--shadow-soft);
		padding: clamp(1rem, 2vw, 1.5rem);
	}

	.status-badge {
		border-radius: 999rem;
		padding: 0.18rem 0.45rem;
		font-size: 0.68rem;
		font-weight: 800;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-text-muted);
		background: var(--color-surface-muted);
		white-space: nowrap;
	}

	.status-badge.complete {
		background: var(--color-status-complete-bg);
		color: var(--color-status-complete);
	}
	.status-badge.failed {
		background: var(--color-status-failed-bg);
		color: var(--color-status-failed);
	}
	.status-badge.running {
		background: var(--color-status-running-bg);
		color: var(--color-status-running);
	}

	h1 {
		font-family: var(--font-display);
		font-size: clamp(1.6rem, 3vw, 2.4rem);
		margin: 0;
		color: var(--color-text);
	}

	.eyebrow {
		font-size: 0.78rem;
		font-weight: 750;
		letter-spacing: 0.04em;
		color: var(--color-accent);
		margin: 0 0 0.2rem;
		text-transform: uppercase;
	}

	.muted {
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}

	button.ghost {
		border-radius: 0.4rem;
		padding: 0.55rem 0.9rem;
		font-weight: 750;
		cursor: pointer;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
	}

	button.ghost:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.detail {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}

	.detail-header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.detail-actions {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}

	.detail-id {
		min-width: 0;
		flex: 1;
	}

	h1.detail-title {
		font-size: clamp(1.15rem, 2vw, 1.5rem);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 100%;
	}

	.detail-meta {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.4rem;
		margin: 0.35rem 0 0;
		font-size: 0.82rem;
		color: var(--color-text-muted);
	}

	.detail-meta .dot {
		color: var(--color-text-dim);
	}

	.running-state {
		display: flex;
		align-items: flex-start;
		gap: 0.75rem;
		padding: 1rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
	}

	.running-state strong,
	.running-state p {
		margin: 0;
	}

	.day-level-note {
		display: flex;
		align-items: flex-start;
		gap: 0.75rem;
		padding: 0.75rem 1rem;
		margin-bottom: 1rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
	}

	.day-level-note.failed {
		border-color: var(--color-danger-border);
	}

	.day-level-text {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		min-width: 0;
		flex: 1;
	}

	.day-level-note strong,
	.day-level-note p {
		margin: 0;
	}

	.day-level-note strong {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	@keyframes breathe {
		50% {
			opacity: 0.35;
		}
	}

	.training-dot {
		width: 0.45rem;
		height: 0.45rem;
		border-radius: 999rem;
		background: var(--color-status-running);
		flex-shrink: 0;
		animation: breathe 2.4s ease-in-out infinite;
	}

	@media (prefers-reduced-motion: reduce) {
		.training-dot {
			animation: none;
		}
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
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

	.error,
	.error-block {
		color: var(--color-danger);
		font-size: 0.85rem;
	}

	.error-block {
		white-space: pre-wrap;
		word-break: break-word;
		font-family: var(--font-mono);
		background: var(--color-danger-bg);
		border: 1px solid var(--color-danger-border);
		border-radius: 0.45rem;
		padding: 0.75rem;
		margin: 0;
	}

	.empty-state {
		padding: clamp(2rem, 8vw, 5rem);
	}

	.empty-title {
		font-weight: 600;
		color: var(--color-text-muted);
		margin: 0 0 0.5rem;
	}

	@media (max-width: 1050px) {
		.workspace-page {
			flex-direction: column;
		}
	}
</style>
