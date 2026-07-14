<script lang="ts">
	import { onDestroy } from 'svelte';
	import {
		listForecasts,
		createForecast,
		listBlends,
		getForecastModels,
		getJobArtifacts,
		getForecastManifest,
		cancelJob,
		deleteJob,
		fetchResultBlob,
		subscribeJob,
		type Blend,
		type Forecast,
		type ForecastCreate,
		type ForecastManifest,
		type ForecastModel,
		type JobArtifact,
		type JobStatus,
		type JobStreamEvent
	} from '$lib/api';
	import RunSidebar, { type RunSection, type RunStatus } from '$lib/components/RunSidebar.svelte';
	import ForecastMap from '$lib/components/ForecastMap.svelte';
	import BlendForecastMap from '$lib/components/BlendForecastMap.svelte';
	import { goto } from '$app/navigation';
	import { account } from '$lib/account.svelte';

	$effect(() => {
		if (account.loaded && !account.canUseForecasting) goto('/');
	});

	const ACTIVE_STATUSES = ['queued', 'starting', 'running', 'canceling'];

	let forecasts = $state<Forecast[]>([]);
	let blends = $state<Blend[]>([]);
	let forecastModels = $state<ForecastModel[]>([]);
	let selectedId = $state<string | null>(null);
	let creating = $state(false);
	let loaded = $state(false);

	const selected = $derived(forecasts.find((f) => f.id === selectedId) ?? null);

	function blendName(blendId: string): string {
		return blends.find((b) => b.id === blendId)?.name || blendId;
	}

	// --- Form state ---
	let blendId = $state('');
	let forecastModelIds = $state<string[]>([]);
	let initTime = $state('');
	// bind:value on <input type="number"> yields a number (or undefined when
	// empty/invalid), not a string — unlike initTime's plain text input.
	let maxLeadDay = $state<number | undefined>(undefined);
	let maxIssueDates = $state<number | undefined>(undefined);
	let submitting = $state(false);
	let submitError = $state<string | null>(null);

	const completedBlends = $derived(blends.filter((b) => b.status === 'complete'));
	const selectedBlend = $derived(blends.find((b) => b.id === blendId) ?? null);

	// Only a blend's own models that also have a live forecast model can be
	// requested — matches the validation create_forecast_for_user enforces.
	const availableModels = $derived.by(() => {
		if (!selectedBlend) return [];
		const registryIds = new Set(forecastModels.map((m) => m.id));
		return forecastModels.filter(
			(m) => selectedBlend.model_names.includes(m.id) && registryIds.has(m.id)
		);
	});

	// Drop any selected model that's no longer available for the chosen blend.
	$effect(() => {
		const ids = new Set(availableModels.map((m) => m.id));
		if (forecastModelIds.some((id) => !ids.has(id))) {
			forecastModelIds = forecastModelIds.filter((id) => ids.has(id));
		}
	});

	const formValid = $derived(blendId !== '' && forecastModelIds.length > 0);

	async function load() {
		const [f, b, m] = await Promise.allSettled([
			listForecasts(),
			listBlends(),
			getForecastModels()
		]);
		if (f.status === 'fulfilled') forecasts = f.value;
		if (b.status === 'fulfilled') blends = b.value;
		if (m.status === 'fulfilled') forecastModels = m.value;
		loaded = true;
	}

	$effect(() => {
		if (!loaded) void load();
	});

	let actionError = $state<string | null>(null);

	async function cancelForecast(id: string) {
		actionError = null;
		try {
			const updated = await cancelJob(id);
			patchStatus(id, updated.status as JobStatus);
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Cancel failed';
		}
	}

	async function deleteForecast(id: string) {
		if (
			!confirm('Delete this forecast? This permanently removes its outputs and cannot be undone.')
		)
			return;
		actionError = null;
		try {
			await deleteJob(id);
			forecasts = forecasts.filter((f) => f.id !== id);
			if (selectedId === id) selectedId = null;
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
			title: 'My Forecasts',
			open: true,
			emptyLabel: loaded ? 'No forecasts yet.' : 'Loading…',
			items: forecasts.map((f) => ({
				id: f.id,
				title: blendName(f.blend_id),
				meta: `${f.forecast_model_ids.length} model${f.forecast_model_ids.length === 1 ? '' : 's'} · ${formatDate(f.created_at)}`,
				count: f.forecast_model_ids.length,
				status: sidebarStatus(f.status),
				canDelete: !ACTIVE_STATUSES.includes(f.status)
			}))
		}
	]);

	const streams = new Map<string, () => void>();
	$effect(() => {
		const active = new Set(
			forecasts.filter((f) => ACTIVE_STATUSES.includes(f.status)).map((f) => f.id)
		);
		for (const id of active) {
			if (!streams.has(id))
				streams.set(
					id,
					subscribeJob(id, (event) => onForecastEvent(id, event))
				);
		}
		for (const [id, close] of streams) {
			if (!active.has(id)) {
				close();
				streams.delete(id);
			}
		}
	});
	onDestroy(() => {
		for (const close of streams.values()) close();
		streams.clear();
	});

	function onForecastEvent(id: string, event: JobStreamEvent) {
		if (event.type !== 'status' && event.type !== 'done') return;
		patchStatus(id, event.payload.status as JobStatus);
		if (event.type === 'done') void refreshForecast(id);
	}

	function patchStatus(id: string, status: JobStatus) {
		const idx = forecasts.findIndex((f) => f.id === id);
		if (idx === -1 || forecasts[idx].status === status) return;
		forecasts[idx] = { ...forecasts[idx], status };
	}

	async function refreshForecast(id: string) {
		try {
			const fresh = (await listForecasts()).find((f) => f.id === id);
			const idx = forecasts.findIndex((f) => f.id === id);
			if (fresh && idx !== -1) forecasts[idx] = fresh;
		} catch {
			/* transient — the status patch already reflects the terminal state */
		}
	}

	function startNew() {
		creating = true;
		selectedId = null;
		submitError = null;
		blendId = '';
		forecastModelIds = [];
		initTime = '';
		maxLeadDay = undefined;
		maxIssueDates = undefined;
	}

	function selectForecast(id: string) {
		selectedId = id;
		creating = false;
	}

	function toggleModel(id: string) {
		forecastModelIds = forecastModelIds.includes(id)
			? forecastModelIds.filter((m) => m !== id)
			: [...forecastModelIds, id];
	}

	async function submit() {
		if (!formValid || submitting) return;
		submitting = true;
		submitError = null;
		try {
			const params: ForecastCreate['params'] = {
				...(initTime.trim() ? { init_time: initTime.trim() } : {}),
				...(maxLeadDay != null && !Number.isNaN(maxLeadDay) ? { max_lead_day: maxLeadDay } : {}),
				...(maxIssueDates != null && !Number.isNaN(maxIssueDates)
					? { max_issue_dates: maxIssueDates }
					: {})
			};
			const body: ForecastCreate = {
				blend_id: blendId,
				forecast_model_ids: forecastModelIds,
				...(Object.keys(params).length ? { params } : {})
			};
			const forecast = await createForecast(body);
			forecasts = [forecast, ...forecasts];
			creating = false;
			selectedId = forecast.id;
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

	// --- Result view: artifacts, per-model manifests, map controls ---
	let artifacts = $state<JobArtifact[]>([]);
	let manifests = $state<Record<string, ForecastManifest | null>>({});
	let activeModelId = $state<string | null>(null);
	let variable = $state('');
	let leadHour = $state(0);

	// Downloadable outputs, excluding the per-model raster/manifest files that
	// back the map viewer (those aren't meant to be downloaded directly).
	const downloadableArtifacts = $derived(
		artifacts.filter(
			(a) => !a.filename.endsWith('manifest.json') && !a.filename.includes('/rasters/')
		)
	);

	$effect(() => {
		const job = selected;
		if (job?.status !== 'complete') {
			artifacts = [];
			manifests = {};
			activeModelId = null;
			return;
		}
		const id = job.id;
		let cancelled = false;
		let attempts = 0;
		const load = async () => {
			if (cancelled) return;
			try {
				const found = await getJobArtifacts(id);
				if (cancelled) return;
				artifacts = found;
				if (found.length === 0 && attempts++ < 6) {
					setTimeout(load, 2000);
					return;
				}
				const loadedManifests: Record<string, ForecastManifest | null> = {};
				for (const modelId of job.forecast_model_ids) {
					loadedManifests[modelId] = await getForecastManifest(modelId, found);
				}
				if (cancelled) return;
				manifests = loadedManifests;
				// Stay on blend tab (null) by default; user can click a model tab.
				if (activeModelId !== null) {
					activeModelId = job.forecast_model_ids.find((id) => loadedManifests[id]) ?? null;
				}
			} catch {
				if (!cancelled) artifacts = [];
			}
		};
		artifacts = [];
		manifests = {};
		void load();
		return () => {
			cancelled = true;
		};
	});

	const activeManifest = $derived(activeModelId ? manifests[activeModelId] : null);

	// Reset variable/lead-hour selection to something valid whenever the
	// active model's manifest changes.
	$effect(() => {
		const m = activeManifest;
		if (!m) return;
		if (!m.variables.includes(variable)) variable = m.variables[0] ?? '';
		if (!m.lead_hours.includes(leadHour)) leadHour = m.lead_hours[0] ?? 0;
	});
</script>

<div class="workspace-page" class:is-setup={creating}>
	{#if !creating}
		<RunSidebar
			newLabel="New forecast"
			{selectedId}
			sections={sidebarSections}
			onNew={startNew}
			onSelect={selectForecast}
			onDelete={deleteForecast}
			deleteTitle="Delete forecast"
		/>
	{/if}

	<div class="workspace-main">
		{#if creating}
			<section class="card form">
				<h1>Run a live forecast</h1>
				<p class="muted">
					Run a completed blend's models forward against the latest conditions and score them
					against the blend's trained weights.
				</p>

				<label class="field">
					<span>Blend</span>
					<select bind:value={blendId}>
						<option value="" disabled>Select a completed blend…</option>
						{#each completedBlends as blend (blend.id)}
							<option value={blend.id}>{blend.name || 'Untitled blend'}</option>
						{/each}
					</select>
					{#if blendId === '' && completedBlends.length === 0}
						<p class="muted">No completed blends yet. Train one under Blends first.</p>
					{/if}
				</label>

				{#if selectedBlend}
					<fieldset class="field">
						<legend>Forecast models</legend>
						{#if availableModels.length === 0}
							<p class="muted">None of this blend's models have a live forecast model available.</p>
						{:else}
							<div class="model-grid">
								{#each availableModels as model (model.id)}
									<label class="checkbox">
										<input
											type="checkbox"
											checked={forecastModelIds.includes(model.id)}
											onchange={() => toggleModel(model.id)}
										/>
										<span>{model.display_name}</span>
									</label>
								{/each}
							</div>
						{/if}
					</fieldset>

					<details class="advanced">
						<summary>Advanced</summary>
						<label class="field">
							<span>Init time (UTC)</span>
							<input type="text" bind:value={initTime} placeholder="defaults to latest available" />
						</label>
						<label class="field">
							<span>Max season lead days</span>
							<input
								type="number"
								min="1"
								bind:value={maxLeadDay}
								placeholder="defaults to full 45-day lead"
							/>
						</label>
						<label class="field">
							<span>Max season issue dates</span>
							<input
								type="number"
								min="1"
								bind:value={maxIssueDates}
								placeholder="defaults to the whole season-to-date"
							/>
						</label>
					</details>
				{/if}

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
						{submitting ? 'Submitting…' : 'Run forecast'}
					</button>
				</div>
			</section>
		{:else if selected}
			<section class="card detail">
				<header class="detail-header">
					<div class="detail-id">
						<p class="eyebrow">Forecast</p>
						<h1 class="forecast-id" title={blendName(selected.blend_id)}>
							{blendName(selected.blend_id)}
						</h1>
						<p class="detail-meta">
							{#if selected.forecast_model_ids.length}
								<span>{selected.forecast_model_ids.join(', ')}</span>
								<span class="dot" aria-hidden="true">·</span>
							{/if}
							<span>Submitted {formatDate(selected.created_at)}</span>
							{#if selected.completed_at}
								<span class="dot" aria-hidden="true">·</span>
								<span>Completed {formatDate(selected.completed_at)}</span>
							{/if}
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
								onclick={() => cancelForecast(selected!.id)}
							>
								{selected.status === 'canceling' ? 'Canceling…' : 'Cancel'}
							</button>
						{/if}
					</div>
				</header>

				{#if actionError}
					<p class="error">{actionError}</p>
				{/if}

				{#if ACTIVE_STATUSES.includes(selected.status)}
					<div class="running-state">
						<div class="spinner"></div>
						<div>
							<strong>Running live inference</strong>
							<p class="muted">Model maps and blended probabilities will appear here.</p>
						</div>
					</div>
				{/if}

				{#if selected.status === 'failed' && selected.error}
					<pre class="error-block">{selected.error}</pre>
				{/if}

				{#if selected.status === 'complete'}
					{#if selected.forecast_model_ids.length > 0}
						<div class="map-section">
							<div class="map-controls">
								<div class="tabs">
									<button
										type="button"
										class="tab"
										class:active={activeModelId === null}
										onclick={() => (activeModelId = null)}
									>
										Monsoon Onset
									</button>
									{#each selected.forecast_model_ids as modelId (modelId)}
										<button
											type="button"
											class="tab"
											class:active={activeModelId === modelId}
											disabled={!manifests[modelId]}
											onclick={() => (activeModelId = modelId)}
										>
											{manifests[modelId]?.model_name ?? modelId}
										</button>
									{/each}
								</div>
								{#if activeModelId !== null && activeManifest}
									<div class="selectors">
										<label>
											Variable
											<select bind:value={variable}>
												{#each activeManifest.variables as v (v)}
													<option value={v}>{v}</option>
												{/each}
											</select>
										</label>
										<label>
											Lead time
											<select bind:value={leadHour}>
												{#each activeManifest.lead_hours as lh (lh)}
													<option value={lh}>+{lh}h</option>
												{/each}
											</select>
										</label>
									</div>
								{/if}
							</div>
							<div class="map-host">
								{#if activeModelId === null}
									<BlendForecastMap jobId={selected.id} />
								{:else if activeModelId && activeManifest}
									{#key activeModelId}
										<ForecastMap
											jobId={selected.id}
											modelId={activeModelId}
											modelName={activeManifest.model_name}
											manifest={activeManifest}
											{variable}
											{leadHour}
											label={variable}
										/>
									{/key}
								{:else}
									<div class="map-empty muted">No map data available.</div>
								{/if}
							</div>
						</div>
					{/if}

					<div class="artifacts">
						<h2>Outputs</h2>
						{#if downloadableArtifacts.length === 0}
							<p class="muted">No downloadable outputs found.</p>
						{:else}
							<ul>
								{#each downloadableArtifacts as artifact (artifact.id)}
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
				<p class="empty-title">No forecast selected</p>
				<p class="muted">
					Pick a forecast from the list, or run a new one against a completed blend.
				</p>
			</div>
		{/if}
	</div>
</div>

<style>
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
		margin-top: 0.5rem;
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

	.detail-id {
		min-width: 0;
		flex: 1;
	}

	h1.forecast-id {
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

	.map-section {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}

	.map-controls {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.tabs {
		display: flex;
		gap: 0.35rem;
		flex-wrap: wrap;
	}

	.tab {
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		padding: 0.4rem 0.7rem;
		font-size: 0.85rem;
		font-weight: 650;
		cursor: pointer;
	}

	.tab.active {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: white;
	}

	.tab:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.selectors {
		display: flex;
		gap: 0.75rem;
	}

	.selectors label {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.75rem;
		font-weight: 700;
		color: var(--color-text-muted);
	}

	.selectors select {
		padding: 0.35rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-bg);
		color: var(--color-text);
		font: inherit;
	}

	.map-host {
		position: relative;
		width: 100%;
		aspect-ratio: 16 / 9;
		border: 1px solid rgba(36, 33, 29, 0.22);
		border-radius: 0.5rem;
		overflow: hidden;
		box-shadow: var(--shadow-soft);
	}

	.map-empty {
		display: grid;
		place-items: center;
		width: 100%;
		height: 100%;
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
