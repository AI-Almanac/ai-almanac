<script lang="ts">
	import { onDestroy } from 'svelte';
	import {
		listBlends,
		createBlend,
		listDataSources,
		getJobArtifacts,
		cancelJob,
		fetchResultBlob,
		type Blend,
		type BlendCreate,
		type DataSource,
		type JobArtifact
	} from '$lib/api';

	const ACTIVE_STATUSES = ['queued', 'starting', 'running', 'canceling'];

	let blends = $state<Blend[]>([]);
	let obsSources = $state<DataSource[]>([]);
	let modelSources = $state<DataSource[]>([]);
	let selectedId = $state<string | null>(null);
	let creating = $state(false);
	let loaded = $state(false);

	let artifacts = $state<JobArtifact[]>([]);
	let artifactsForId = $state<string | null>(null);

	const selected = $derived(blends.find((b) => b.id === selectedId) ?? null);
	const hasActive = $derived(blends.some((b) => ACTIVE_STATUSES.includes(b.status)));

	// --- Form state ---
	let name = $state('');
	let obsDatasetId = $state('');
	let modelIds = $state<string[]>([]);
	let trainingYears = $state('');
	let cvHoldoutYears = $state('');
	let forecastYears = $state('');
	let trueHoldoutYears = $state('');
	let formulaText = $state('');
	let submitting = $state(false);
	let submitError = $state<string | null>(null);

	const formValid = $derived(
		name.trim() !== '' &&
			obsDatasetId !== '' &&
			modelIds.length > 0 &&
			trainingYears.trim() !== '' &&
			cvHoldoutYears.trim() !== ''
	);

	async function load() {
		const [b, obs, models] = await Promise.allSettled([
			listBlends(),
			listDataSources('obs'),
			listDataSources('model')
		]);
		if (b.status === 'fulfilled') blends = b.value;
		if (obs.status === 'fulfilled') obsSources = obs.value.filter((s) => s.status === 'ready');
		if (models.status === 'fulfilled')
			modelSources = models.value.filter((s) => s.status === 'ready');
		loaded = true;
	}

	let polling: ReturnType<typeof setInterval> | null = null;
	$effect(() => {
		if (hasActive && !polling) {
			polling = setInterval(refreshBlends, 5000);
		} else if (!hasActive && polling) {
			clearInterval(polling);
			polling = null;
		}
	});
	onDestroy(() => polling && clearInterval(polling));

	async function refreshBlends() {
		try {
			blends = await listBlends();
		} catch {
			/* transient — next tick retries */
		}
	}

	$effect(() => {
		if (!loaded) void load();
	});

	// Load artifacts when a completed blend is selected.
	$effect(() => {
		if (selected?.status === 'complete' && selected.id !== artifactsForId) {
			const id = selected.id;
			artifactsForId = id;
			void getJobArtifacts(id)
				.then((a) => (artifacts = a))
				.catch(() => (artifacts = []));
		}
	});

	function startNew() {
		creating = true;
		selectedId = null;
		submitError = null;
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

<div class="page-layout">
	<aside class="sidebar">
		<button type="button" class="primary" onclick={startNew}>New blend</button>
		<ul class="blend-list">
			{#each blends as blend (blend.id)}
				<li>
					<button
						type="button"
						class="blend-item"
						class:selected={blend.id === selectedId}
						onclick={() => selectBlend(blend.id)}
					>
						<span class="blend-name">{blend.name || 'Untitled blend'}</span>
						<span class="status-badge {statusClass(blend.status)}">{statusLabel(blend.status)}</span
						>
						<span class="blend-meta"
							>{blend.model_names.length} model{blend.model_names.length === 1 ? '' : 's'} · {formatDate(
								blend.created_at
							)}</span
						>
					</button>
				</li>
			{/each}
			{#if loaded && blends.length === 0}
				<li class="muted">No blends yet</li>
			{/if}
		</ul>
	</aside>

	<div class="main-content">
		{#if creating}
			<section class="card form">
				<h1>Train a blend</h1>
				<p class="muted">
					Combine multiple forecast models into a single blended forecast. Training learns the
					weights and saves them as a downloadable artifact.
				</p>

				<label class="field">
					<span>Blend name</span>
					<input type="text" bind:value={name} placeholder="e.g. India monsoon blend" />
				</label>

				<label class="field">
					<span>Observations</span>
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
					<legend>Forecast models</legend>
					{#if modelSources.length === 0}
						<p class="muted">No ready model sources. Add forecast models under Data first.</p>
					{:else}
						<div class="model-grid">
							{#each modelSources as source (source.id)}
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

				<div class="field-row">
					<label class="field">
						<span>Training years</span>
						<input type="text" bind:value={trainingYears} placeholder="2015:2020" />
					</label>
					<label class="field">
						<span>CV holdout years</span>
						<input type="text" bind:value={cvHoldoutYears} placeholder="2021,2022" />
					</label>
				</div>

				<details class="advanced">
					<summary>Advanced</summary>
					<div class="field-row">
						<label class="field">
							<span>Forecast years</span>
							<input
								type="text"
								bind:value={forecastYears}
								placeholder="defaults to training + holdout"
							/>
						</label>
						<label class="field">
							<span>True holdout years</span>
							<input type="text" bind:value={trueHoldoutYears} placeholder="optional" />
						</label>
					</div>
					<label class="field">
						<span>Formula</span>
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
		{:else if selected}
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
						{#if ACTIVE_STATUSES.includes(selected.status) && selected.status !== 'canceling'}
							<button type="button" class="ghost" onclick={() => cancelJob(selected!.id)}
								>Cancel</button
							>
						{/if}
					</div>
				</header>

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
	.page-layout {
		width: min(100% - 2rem, 76rem);
		margin: 0 auto;
		padding: 1.25rem 0 2rem;
		display: flex;
		gap: 1.25rem;
		align-items: flex-start;
	}

	.sidebar {
		flex: 0 0 18rem;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.main-content {
		flex: 1;
		min-width: 0;
	}

	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		box-shadow: var(--shadow-soft);
		padding: clamp(1rem, 2vw, 1.5rem);
	}

	.blend-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.blend-item {
		display: grid;
		grid-template-columns: 1fr auto;
		gap: 0.25rem 0.5rem;
		width: 100%;
		text-align: left;
		padding: 0.6rem 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-surface);
		cursor: pointer;
	}

	.blend-item:hover,
	.blend-item.selected {
		border-color: var(--color-accent-border);
	}

	.blend-name {
		font-weight: 700;
		color: var(--color-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.blend-meta {
		grid-column: 1 / -1;
		color: var(--color-text-muted);
		font-size: 0.78rem;
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

	@media (max-width: 820px) {
		.page-layout {
			flex-direction: column;
		}

		.sidebar {
			flex-basis: auto;
			width: 100%;
		}
	}
</style>
