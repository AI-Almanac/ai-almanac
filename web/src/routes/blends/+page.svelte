<script lang="ts">
	import { pollWhileActive } from '$lib/poll';
	import { page } from '$app/stores';
	import {
		listBlends,
		createBlend,
		listDataSources,
		getCapabilities,
		getJobArtifacts,
		getBlendSummary,
		cancelJob,
		deleteJob,
		fetchResultBlob,
		type Blend,
		type BlendCreate,
		type BlendRunSpec,
		type ChatScope,
		type DataSource,
		type Job,
		type JobArtifact,
		type JobStatus
	} from '$lib/api';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import RunSidebar, { type RunSection, type RunStatus } from '$lib/components/RunSidebar.svelte';
	import { MIN_ONSET_YEARS, computeCoverage, defaultSplit, yearSpecError } from './year-coverage';
	import { parsePooledSummary, type SkillRow } from './blend-summary';
	import BlendSkillChart from './BlendSkillChart.svelte';

	const ACTIVE_STATUSES = ['queued', 'starting', 'running', 'canceling'];

	let blends = $state<Blend[]>([]);
	let obsSources = $state<DataSource[]>([]);
	let modelSources = $state<DataSource[]>([]);
	let selectedId = $state<string | null>(null);
	let creating = $state(false);
	let loaded = $state(false);
	let chatAvailable = $state(false);
	const blendSetupKey = crypto.randomUUID();

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
		if (chatId && scopeKind && scopeKey) {
			continuedSessionId = chatId;
			continuedScope = { kind: scopeKind, key: scopeKey };
			continuedBlendId = blendId;
		}
	});

	let artifacts = $state<JobArtifact[]>([]);
	let skill = $state<SkillRow[]>([]);

	const selected = $derived(blends.find((b) => b.id === selectedId) ?? null);
	const hasActive = $derived(blends.some((b) => ACTIVE_STATUSES.includes(b.status)));

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

	// --- Form state ---
	let name = $state('');
	let obsDatasetId = $state('');
	let modelIds = $state<string[]>([]);
	let trainingYears = $state('');
	let cvHoldoutYears = $state('');
	let forecastYears = $state('');
	let trueHoldoutYears = $state('');
	let formulaText = $state('');
	let yearsDirty = $state(false);
	let submitting = $state(false);
	let submitError = $state<string | null>(null);

	const selectedObs = $derived(obsSources.find((s) => s.id === obsDatasetId) ?? null);

	// Models are region-specific: only offer ones matching the chosen observation
	// dataset's region, mirroring the benchmark setup flow.
	const availableModels = $derived(
		selectedObs ? modelSources.filter((s) => s.region === selectedObs.region) : []
	);

	// Drop any selected model that no longer matches the chosen region.
	$effect(() => {
		const ids = new Set(availableModels.map((s) => s.id));
		if (modelIds.some((id) => !ids.has(id))) {
			modelIds = modelIds.filter((id) => ids.has(id));
		}
	});

	const coverage = $derived(
		computeCoverage(
			selectedObs ?? undefined,
			modelSources.filter((s) => modelIds.includes(s.id))
		)
	);
	const insufficientData = $derived(coverage != null && coverage.earliestForecast > coverage.end);

	const yearError = $derived(
		coverage
			? yearSpecError(coverage, trainingYears, cvHoldoutYears, forecastYears, trueHoldoutYears)
			: null
	);

	// Prefill a working training/holdout split once obs + models are chosen, until
	// the user edits the year fields themselves.
	$effect(() => {
		if (!coverage || yearsDirty) return;
		const split = defaultSplit(coverage);
		if (!split) return;
		trainingYears = split.training;
		cvHoldoutYears = split.cv;
	});

	const formValid = $derived(
		name.trim() !== '' &&
			obsDatasetId !== '' &&
			modelIds.length > 0 &&
			trainingYears.trim() !== '' &&
			cvHoldoutYears.trim() !== '' &&
			!yearError &&
			!insufficientData
	);

	async function load() {
		const [b, obs, models, caps] = await Promise.allSettled([
			listBlends(),
			listDataSources('obs'),
			listDataSources('model'),
			getCapabilities()
		]);
		if (b.status === 'fulfilled') blends = b.value;
		if (obs.status === 'fulfilled') obsSources = obs.value.filter((s) => s.status === 'ready');
		if (models.status === 'fulfilled')
			modelSources = models.value.filter((s) => s.status === 'ready');
		if (caps.status === 'fulfilled') chatAvailable = caps.value.chat;
		loaded = true;
	}

	// Mirror an LLM-produced blend config into the manual form so the two stay in
	// sync — the user can hand off to chat and keep editing fields directly.
	function applyBlendConfig(config: BlendRunSpec) {
		name = config.name ?? '';
		obsDatasetId = config.obs_dataset_id ?? '';
		modelIds = config.model_ids ?? [];
		trainingYears = config.training_years ?? '';
		cvHoldoutYears = config.cv_holdout_years ?? '';
		forecastYears = config.forecast_years ?? '';
		trueHoldoutYears = config.true_holdout_years ?? '';
		formulaText = config.formula_text ?? '';
		if (config.training_years || config.cv_holdout_years) yearsDirty = true;
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
			actionError = err instanceof Error ? err.message : 'Cancel failed';
		}
	}

	async function deleteBlend(id: string) {
		const blend = blends.find((b) => b.id === id);
		if (!blend) return;
		const label = blend.name || 'this blend';
		if (
			!confirm(
				`Delete "${label}"? This permanently removes its trained weights and outputs from storage and cannot be undone.`
			)
		) {
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
			actionError = err instanceof Error ? err.message : 'Delete failed';
		}
	}

	function sidebarStatus(status: string): RunStatus {
		if (status === 'complete') return 'complete';
		if (status === 'failed') return 'failed';
		if (status === 'canceled') return 'canceled';
		if (ACTIVE_STATUSES.includes(status)) return 'running';
		return 'mixed';
	}

	const sidebarSections = $derived<RunSection[]>([
		{
			title: 'My Blends',
			open: true,
			emptyLabel: loaded ? 'No blends yet.' : 'Loading…',
			items: blends.map((b) => ({
				id: b.id,
				title: b.name || 'Untitled blend',
				meta: `${b.model_names.length} model${b.model_names.length === 1 ? '' : 's'} · ${formatDate(b.created_at)}`,
				count: b.model_names.length,
				status: sidebarStatus(b.status),
				canDelete: !ACTIVE_STATUSES.includes(b.status)
			}))
		}
	]);

	// Stream status changes per running blend instead of re-fetching the whole
	// list on a timer. Replacing the array on every poll reassigned every blend
	// object, which remounted the detail view and made the page jump. Here only
	// the blend whose status actually changed is rewritten.
	// While any blend is active, re-fetch the list and replace only the blends
	// whose status changed (a wholesale rewrite remounts the detail view and
	// makes the page jump). Bounded by the server's ~5s reconcile cadence, so a
	// 3s poll is as live as the data gets. Returning the stop fn lets $effect
	// tear the timer down on unmount.
	$effect(() =>
		pollWhileActive(() => blends.some((b) => ACTIVE_STATUSES.includes(b.status)), syncBlendStatuses)
	);

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
			return updated && updated.status !== b.status ? updated : b;
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
	// lags completion by a few seconds, so an empty result is retried until the
	// artifacts land rather than cached as "none".
	$effect(() => {
		const job = selected;
		if (job?.status !== 'complete') return;
		const id = job.id;
		let cancelled = false;
		let attempts = 0;
		const load = async () => {
			if (cancelled) return;
			try {
				const found = await getJobArtifacts(id);
				if (cancelled) return;
				artifacts = found;
				if (found.length === 0 && attempts++ < 6) setTimeout(load, 2000);
			} catch {
				if (!cancelled) artifacts = [];
			}
		};
		artifacts = [];
		void load();
		return () => {
			cancelled = true;
		};
	});

	// Parse the small pooled per-model summary into the skill chart series. The
	// CSV is read by the backend (never fetched from the bucket by the browser);
	// keyed on artifacts so it re-runs once publication indexes the summary.
	$effect(() => {
		const job = selected;
		const hasSummary = artifacts.some((a) => a.filename.startsWith('summary_models_pooled'));
		if (job?.status !== 'complete' || !hasSummary) {
			skill = [];
			return;
		}
		let cancelled = false;
		void (async () => {
			try {
				const text = await getBlendSummary(job.id);
				if (!cancelled) skill = parsePooledSummary(text);
			} catch {
				if (!cancelled) skill = [];
			}
		})();
		return () => {
			cancelled = true;
		};
	});

	function startNew() {
		creating = true;
		selectedId = null;
		submitError = null;
		yearsDirty = false;
	}

	function selectBlend(id: string) {
		selectedId = id;
		creating = false;
	}

	function toggleModel(id: string) {
		modelIds = modelIds.includes(id) ? modelIds.filter((m) => m !== id) : [...modelIds, id];
	}

	async function submit() {
		if (!formValid || submitting) return;
		submitting = true;
		submitError = null;
		const body: BlendCreate = {
			name: name.trim(),
			obs_dataset_id: obsDatasetId,
			model_ids: modelIds,
			params: {
				training_years: trainingYears.trim(),
				cv_holdout_years: cvHoldoutYears.trim(),
				...(forecastYears.trim() ? { forecast_years: forecastYears.trim() } : {}),
				...(trueHoldoutYears.trim() ? { true_holdout_years: trueHoldoutYears.trim() } : {}),
				...(formulaText.trim() ? { formula_text: formulaText.trim() } : {})
			}
		};
		try {
			const blend = await createBlend(body);
			blends = [blend, ...blends];
			creating = false;
			selectedId = blend.id;
			name = obsDatasetId = trainingYears = cvHoldoutYears = '';
			forecastYears = trueHoldoutYears = formulaText = '';
			modelIds = [];
			yearsDirty = false;
		} catch (err) {
			submitError = err instanceof Error ? err.message : 'Submission failed';
		} finally {
			submitting = false;
		}
	}

	async function downloadArtifact(artifact: JobArtifact) {
		const objectUrl = await fetchResultBlob(artifact.url);
		const a = document.createElement('a');
		a.href = objectUrl;
		a.download = artifact.filename;
		a.click();
	}

	function statusLabel(status: string): string {
		if (status === 'canceling') return 'Canceling';
		if (ACTIVE_STATUSES.includes(status)) return 'Running';
		return status.charAt(0).toUpperCase() + status.slice(1);
	}

	function statusClass(status: string): string {
		if (status === 'complete') return 'complete';
		if (status === 'failed') return 'failed';
		if (ACTIVE_STATUSES.includes(status)) return 'running';
		return '';
	}

	function formatDate(value?: string | null): string {
		if (!value) return '—';
		const d = new Date(value);
		if (Number.isNaN(d.getTime())) return value;
		return new Intl.DateTimeFormat(undefined, {
			month: 'short',
			day: 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		}).format(d);
	}
</script>

{#snippet fieldLabel(text: string, tip: string)}
	<span class="label-with-help">
		{text}
		<span class="tip" title={tip}>ⓘ</span>
	</span>
{/snippet}

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
		{#if creating}
			<div class="setup-layout" class:with-chat={chatAvailable}>
				{#if chatAvailable}
					<div class="setup-chat">
						<ChatPanel
							jobs={[]}
							scopeKind="blend_setup"
							scopeKey={blendSetupKey}
							title="Blend setup"
							emptyMessage="Describe the blend you want, or ask which models to combine based on your benchmark results."
							placeholder="Ask for the blend you want, or a question…"
							suggestions={[
								'Blend the best monsoon-onset models for India',
								'Which models should I combine based on my benchmarks?',
								'What do the training and CV holdout years mean?'
							]}
							showArtifacts={false}
							onBlendConfig={applyBlendConfig}
							onBlendSubmitted={handleBlendSubmitted}
						/>
					</div>
				{/if}
				<section class="card form">
					<h1>Train a blend</h1>
					<p class="muted">
						Combine multiple forecast models into a single blended forecast. Training learns the
						weights and saves them as a downloadable artifact.
					</p>

					<label class="field">
						{@render fieldLabel(
							'Blend name',
							'A label to identify this blend in your list. Does not affect training.'
						)}
						<input type="text" bind:value={name} placeholder="e.g. India monsoon blend" />
					</label>

					<label class="field">
						{@render fieldLabel(
							'Observations',
							'Ground-truth rainfall used both to score the forecasts and to build the onset climatology baseline. Earlier coverage allows earlier forecast years.'
						)}
						<select bind:value={obsDatasetId}>
							<option value="" disabled>Select an observation source…</option>
							{#each obsSources as source (source.id)}
								<option value={source.id}
									>{source.name}{source.region ? ` (${source.region})` : ''}</option
								>
							{/each}
						</select>
					</label>

					<fieldset class="field">
						<legend class="label-with-help">
							Forecast models
							<span
								class="tip"
								title="Select two or more forecast models to combine. Training learns how much weight each model gets in the blend."
								>ⓘ</span
							>
						</legend>
						{#if !selectedObs}
							<p class="muted">Select an observation source first to see matching models.</p>
						{:else if availableModels.length === 0}
							<p class="muted">
								No ready forecast models for this region. Add some under Data first.
							</p>
						{:else}
							<div class="model-grid">
								{#each availableModels as source (source.id)}
									<label class="checkbox">
										<input
											type="checkbox"
											checked={modelIds.includes(source.id)}
											onchange={() => toggleModel(source.id)}
										/>
										<span>{source.name}{source.region ? ` (${source.region})` : ''}</span>
									</label>
								{/each}
							</div>
						{/if}
					</fieldset>

					{#if coverage}
						<p class="muted coverage-hint">
							Shared data: {coverage.start}–{coverage.end}. Forecast years start at {coverage.earliestForecast}
							(leaves {MIN_ONSET_YEARS} years for climatology).
						</p>
					{/if}

					<div class="field-row">
						<label class="field">
							{@render fieldLabel(
								'Training years',
								`Years used to fit the blending weights, e.g. "2008:2010". Requires at least ${MIN_ONSET_YEARS} years of observations before the first forecast year for the climatology baseline.`
							)}
							<input
								type="text"
								bind:value={trainingYears}
								oninput={() => (yearsDirty = true)}
								placeholder="2015:2020"
							/>
						</label>
						<label class="field">
							{@render fieldLabel(
								'CV holdout years',
								'Years held out of training to cross-validate the weights, e.g. "2011,2012". Usually the most recent 1–2 years of the shared range.'
							)}
							<input
								type="text"
								bind:value={cvHoldoutYears}
								oninput={() => (yearsDirty = true)}
								placeholder="2021,2022"
							/>
						</label>
					</div>

					{#if insufficientData}
						<p class="error">
							These sources don't have {MIN_ONSET_YEARS} years of observations before any shared forecast
							year. Pick an observation source with earlier coverage.
						</p>
					{:else if yearError}
						<p class="error">{yearError}</p>
					{/if}

					<details class="advanced">
						<summary>Advanced</summary>
						<div class="field-row">
							<label class="field">
								{@render fieldLabel(
									'Forecast years',
									'Years to stage and score, if different from training + holdout. Defaults to the union of training and holdout years.'
								)}
								<input
									type="text"
									bind:value={forecastYears}
									placeholder="defaults to training + holdout"
								/>
							</label>
							<label class="field">
								{@render fieldLabel(
									'True holdout years',
									'Years never shown during training or cross-validation, reserved for a final unbiased evaluation. Optional.'
								)}
								<input type="text" bind:value={trueHoldoutYears} placeholder="optional" />
							</label>
						</div>
						<label class="field">
							{@render fieldLabel(
								'Formula',
								'Advanced override for the statistical formula combining the models. Leave blank to use the default blend formula.'
							)}
							<input
								type="text"
								bind:value={formulaText}
								placeholder="optional — model formula override"
							/>
						</label>
					</details>

					{#if submitError}
						<p class="error">{submitError}</p>
					{/if}

					<div class="form-actions">
						<button type="button" class="ghost" onclick={() => (creating = false)}>Cancel</button>
						<button
							type="button"
							class="primary"
							disabled={!formValid || submitting}
							onclick={submit}
						>
							{submitting ? 'Submitting…' : 'Train blend'}
						</button>
					</div>
				</section>
			</div>
		{:else if selected}
			<div class="workspace-split" class:is-solo={!(chatAvailable && detailChat)}>
				<section class="card detail">
					<header class="detail-header">
						<div>
							<p class="eyebrow">Blend</p>
							<h1>{selected.name || 'Untitled blend'}</h1>
							<p class="muted">
								{selected.model_names.join(', ')}
								{#if selected.region_id}· {selected.region_id}{/if}
							</p>
						</div>
						<div class="detail-actions">
							<span class="status-badge {statusClass(selected.status)}"
								>{statusLabel(selected.status)}</span
							>
							{#if ACTIVE_STATUSES.includes(selected.status)}
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

					<dl class="facts">
						<div>
							<dt>Submitted</dt>
							<dd>{formatDate(selected.created_at)}</dd>
						</div>
						<div>
							<dt>Completed</dt>
							<dd>{formatDate(selected.completed_at)}</dd>
						</div>
						<div>
							<dt>Models</dt>
							<dd>{selected.model_names.length}</dd>
						</div>
					</dl>

					{#if ACTIVE_STATUSES.includes(selected.status)}
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
						{#if skill.length > 0}
							<div class="skill">
								<h2>Forecast skill</h2>
								<BlendSkillChart series={skill} />
							</div>
						{/if}

						<div class="artifacts">
							<h2>Weights & outputs</h2>
							{#if artifacts.length === 0}
								<p class="muted">No artifacts found.</p>
							{:else}
								<ul>
									{#each artifacts as artifact (artifact.id)}
										<li>
											<button
												type="button"
												class="artifact"
												onclick={() => downloadArtifact(artifact)}
											>
												<span class="artifact-name">{artifact.filename}</span>
												<span class="muted">{(artifact.size_bytes / 1024).toFixed(0)} KB</span>
											</button>
										</li>
									{/each}
								</ul>
							{/if}
						</div>
					{/if}
				</section>
				{#if chatAvailable && detailChat}
					<aside class="workspace-aside">
						<div class="result-chat">
							<ChatPanel
								jobs={detailChatJobs}
								scopeKind={detailChat.scopeKind}
								scopeKey={detailChat.scopeKey}
								preferredSessionId={detailChat.sessionId}
								title={selected.name || 'Blend'}
								emptyMessage="Ask about this blend or its training results."
								placeholder="Ask about this blend…"
								suggestions={[
									'How does the blend compare to the individual models?',
									'Which model contributes most to the blend?',
									'Summarise the forecast skill of this blend.'
								]}
								showArtifacts={false}
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
		min-height: 32rem;
	}

	.setup-chat :global(.chat-panel) {
		width: 100%;
		min-height: 100%;
		box-shadow: var(--shadow-soft);
	}

	.setup-layout.with-chat .form {
		flex: 1 1 50%;
		min-width: 0;
		align-self: flex-start;
	}

	@media (max-width: 60rem) {
		.setup-layout.with-chat {
			flex-direction: column;
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

	.form {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.field > span,
	.field legend {
		font-size: 0.8rem;
		font-weight: 700;
		color: var(--color-text);
	}

	.label-with-help {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}

	.tip {
		display: inline-grid;
		place-items: center;
		width: 1rem;
		height: 1rem;
		border-radius: 999px;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font-size: 0.7rem;
		font-weight: 900;
		cursor: help;
	}

	.field input,
	.field select {
		padding: 0.55rem 0.65rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-bg);
		color: var(--color-text);
		font: inherit;
	}

	fieldset.field {
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.45rem;
		padding: 0.75rem;
	}

	.model-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr));
		gap: 0.4rem;
	}

	.checkbox {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.9rem;
		color: var(--color-text);
	}

	.field-row {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.field-row .field {
		flex: 1;
		min-width: 12rem;
	}

	.advanced summary {
		cursor: pointer;
		font-weight: 700;
		font-size: 0.85rem;
		color: var(--color-text);
	}

	.advanced[open] {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.form-actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.6rem;
	}

	button.primary,
	button.ghost {
		border-radius: 0.4rem;
		padding: 0.55rem 0.9rem;
		font-weight: 750;
		cursor: pointer;
		border: 1px solid var(--color-border);
	}

	button.ghost:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	button.primary {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: white;
	}

	button.primary:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	button.ghost {
		background: var(--color-surface);
		color: var(--color-text);
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

	.facts {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr));
		gap: 0.6rem;
		margin: 0;
	}

	.facts div {
		padding: 0.65rem 0.7rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.45rem;
		background: var(--color-bg);
	}

	.facts dt {
		color: var(--color-text-muted);
		font-size: 0.72rem;
		font-weight: 750;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		margin-bottom: 0.2rem;
	}

	.facts dd {
		margin: 0;
		color: var(--color-text);
		font-weight: 650;
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

	.artifacts ul {
		list-style: none;
		margin: 0.5rem 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.artifact {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 1rem;
		width: 100%;
		padding: 0.6rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-bg);
		cursor: pointer;
		text-align: left;
	}

	.artifact:hover {
		border-color: var(--color-accent-border);
	}

	.artifact-name {
		font-weight: 650;
		color: var(--color-text);
		font-family: var(--font-mono);
		font-size: 0.85rem;
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
