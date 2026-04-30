<script lang="ts">
	import { type BenchmarkStore } from '$lib/benchmarks.svelte';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import {
		getModels,
		submitChatBenchmark,
		updateChatBenchmarkConfig,
		type BenchmarkRunSpec,
		type BenchmarkValidation,
		type Dataset,
		type Job,
		type ModelConfig,
		type Region
	} from '$lib/api';

	let {
		store,
		regions,
		datasets,
		dataLoaded,
		initialPrompt = '',
		onSubmitted
	}: {
		store: BenchmarkStore;
		regions: Region[];
		datasets: Dataset[];
		dataLoaded: boolean;
		initialPrompt?: string;
		onSubmitted: (groupKey: string, chatSessionId?: string | null) => void;
	} = $props();

	const setupKey = crypto.randomUUID();

	let spec = $state<BenchmarkRunSpec | null>(null);
	let validation = $state<BenchmarkValidation | null>(null);
	let models = $state<ModelConfig[]>([]);
	let selectedRegionId = $state('');
	let selectedDatasetId = $state('');
	let selectedModelIds = $state<string[]>([]);
	let forecastWindowDays = $state<number | null>(30);
	let detailsOpen = $state(true);
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let chatSessionId = $state<string | null>(null);

	const selectedRegion = $derived(regions.find((region) => region.id === selectedRegionId) ?? null);
	const selectedDataset = $derived(
		datasets.find((dataset) => dataset.id === selectedDatasetId) ?? null
	);
	const selectedModels = $derived(models.filter((model) => selectedModelIds.includes(model.id)));
	const hasPlan = $derived(Boolean(spec));
	const runState = $derived(submitting ? 'running' : (spec?.status ?? 'collecting'));
	const canRun = $derived(
		Boolean(
			(validation?.can_run ?? false) ||
				(spec && selectedRegionId && selectedDatasetId && selectedModelIds.length > 0)
		)
	);
	const specSlots = $derived([
		{ label: 'Question', value: spec?.intent || 'Waiting for prompt' },
		{ label: 'Region', value: selectedRegion?.display_name ?? spec?.region_name ?? 'Not set' },
		{ label: 'Ground truth', value: selectedDataset?.name ?? spec?.dataset_name ?? 'Not set' },
		{
			label: 'Models',
			value:
				selectedModels.length > 0
					? selectedModels.map((model) => model.display_name).join(', ')
					: (spec?.model_names.join(', ') ?? 'Not set')
		},
		{
			label: 'Forecast window',
			value: forecastWindowDays ? `Days 1-${forecastWindowDays}` : 'All days'
		}
	]);

	$effect(() => {
		if (!selectedRegion) return;
		getModels(selectedRegion.id).then((fetchedModels) => {
			models = fetchedModels;
		});
	});

	function applySpec(nextSpec: BenchmarkRunSpec, nextValidation?: BenchmarkValidation | null) {
		spec = nextSpec;
		validation = nextValidation ?? validation;
		selectedRegionId = nextSpec.region_id ?? '';
		selectedDatasetId = nextSpec.dataset_id ?? '';
		selectedModelIds = nextSpec.model_ids;
		forecastWindowDays = nextSpec.forecast_window_days ?? null;
		if (nextSpec.status === 'runnable') detailsOpen = true;
	}

	function toggleModel(id: string) {
		selectedModelIds = selectedModelIds.includes(id)
			? selectedModelIds.filter((modelId) => modelId !== id)
			: [...selectedModelIds, id];
	}

	function configPatch(): Partial<BenchmarkRunSpec> {
		return {
			intent: spec?.intent ?? '',
			region_id: selectedRegionId || null,
			dataset_id: selectedDatasetId || null,
			model_ids: selectedModelIds,
			event_type: spec?.event_type ?? 'monsoon_onset',
			forecast_window_days: forecastWindowDays
		};
	}

	async function runBenchmark() {
		if (!canRun) {
			error = 'The benchmark plan is missing required fields.';
			return;
		}
		if (!chatSessionId) {
			error = 'The chat session is still loading.';
			return;
		}
		submitting = true;
		error = null;
		try {
			const updated = await updateChatBenchmarkConfig(chatSessionId, configPatch());
			applySpec(updated.benchmark_config, updated.benchmark_validation);
			const response = await submitChatBenchmark(chatSessionId);
			applySpec(response.benchmark_config, response.benchmark_validation);
			handleBenchmarkSubmitted(response.run_id, response.jobs, chatSessionId);
		} catch (e: any) {
			error = e.message ?? 'Benchmark submit failed.';
			submitting = false;
		}
	}

	function handleBenchmarkSubmitted(runId: string, jobs: Job[], sessionId: string | null) {
		submitting = false;
		store.acceptSubmittedJobs(runId, jobs);
		onSubmitted(runId, sessionId);
	}
</script>

<section class="setup-workspace" class:has-plan={hasPlan && detailsOpen}>
	<div class="setup-chat">
		<ChatPanel
			jobs={[]}
			scopeKind="benchmark_setup"
			scopeKey={setupKey}
			title="Benchmark setup"
			emptyMessage="Describe the benchmark you want, ask what the options mean, or start from one of the examples."
			placeholder="Ask for the benchmark you want, or ask a question…"
			suggestions={[
				'Compare monsoon onset skill over southern India',
				'Benchmark Kiremt onset forecasts in Ethiopia',
				'What does climatology mean in this context?'
			]}
			initialMessage={initialPrompt}
			showArtifacts={false}
			onSessionReady={(id) => (chatSessionId = id)}
			onBenchmarkConfig={applySpec}
			onBenchmarkSubmitted={handleBenchmarkSubmitted}
		/>
	</div>

	{#if hasPlan && detailsOpen}
		<aside class="review-panel">
			<div class="review-header">
				<div class="state-row">
					<p class="eyebrow">Benchmark state</p>
					<span class="state-pill" class:runnable={runState === 'runnable'}>
						{runState === 'runnable'
							? 'Runnable'
							: runState === 'running'
								? 'Starting'
								: 'Needs info'}
					</span>
				</div>
				<h2>{selectedRegion?.display_name ?? spec?.region_name ?? 'No region selected'}</h2>
				<p>{spec?.event_type ?? 'monsoon_onset'}</p>
			</div>

			<div class="spec-list">
				{#each specSlots as slot}
					<div>
						<span>{slot.label}</span>
						<strong>{slot.value}</strong>
					</div>
				{/each}
			</div>

			<details class="edit-plan">
				<summary>Edit plan</summary>
				<div class="plan-fields">
					<label>
						<span>Region</span>
						<select bind:value={selectedRegionId}>
							<option value="">Choose a region</option>
							{#each regions.filter((region) => region.has_data) as region}
								<option value={region.id}>{region.display_name}</option>
							{/each}
						</select>
					</label>

					<label>
						<span>Observations</span>
						<select bind:value={selectedDatasetId} disabled={!dataLoaded || datasets.length === 0}>
							{#if datasets.length === 0}
								<option value="">{dataLoaded ? 'No datasets available' : 'Loading datasets...'}</option>
							{:else}
								{#each datasets as dataset}
									<option value={dataset.id}>{dataset.name}</option>
								{/each}
							{/if}
						</select>
					</label>

					<label>
						<span>Forecast window</span>
						<select bind:value={forecastWindowDays}>
							<option value={15}>Days 1-15</option>
							<option value={30}>Days 1-30</option>
							<option value={45}>Days 1-45</option>
						</select>
					</label>
				</div>
			</details>

			<details class="edit-plan">
				<summary>Advanced model and parameter controls</summary>
				<div class="model-section">
					<div class="section-title">
						<span>Models</span>
						<small>{selectedModelIds.length} selected</small>
					</div>
					{#if selectedRegion && models.length === 0}
						<p class="muted">Loading available models...</p>
					{:else if !selectedRegion}
						<p class="muted">Choose a region to load models.</p>
					{:else}
						<div class="model-list">
							{#each models as model}
								<label class="model-option">
									<input
										type="checkbox"
										checked={selectedModelIds.includes(model.id)}
										onchange={() => toggleModel(model.id)}
									/>
									<span>
										<strong>{model.display_name}</strong>
										<small>{model.model_type}{model.probabilistic ? ' · probabilistic' : ''}</small>
									</span>
								</label>
							{/each}
						</div>
					{/if}
				</div>
			</details>

			{#if validation?.errors.length}
				<div class="form-error">
					{#each validation.errors as validationError}
						<p>{validationError}</p>
					{/each}
				</div>
			{:else if error}
				<p class="form-error">{error}</p>
			{/if}

			<button class="run-button" type="button" disabled={!canRun || submitting} onclick={runBenchmark}>
				{submitting ? 'Starting run...' : 'Run benchmark'}
			</button>
		</aside>
	{/if}
</section>

<style>
	.setup-workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: clamp(1rem, 3vw, 2rem);
		height: calc(100vh - 9rem);
		min-height: 34rem;
	}

	.setup-workspace.has-plan {
		grid-template-columns: minmax(0, 1fr) minmax(22rem, 30rem);
	}

	.setup-chat {
		min-width: 0;
		min-height: 0;
		display: flex;
	}

	.setup-chat :global(.chat-panel) {
		min-height: 100%;
		box-shadow: var(--shadow-soft);
	}

	.review-panel {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		min-height: 0;
		padding: clamp(1rem, 2vw, 1.25rem);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		box-shadow: var(--shadow-soft);
	}

	.review-header h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: clamp(1.5rem, 3vw, 2rem);
		font-weight: 650;
		line-height: 1.05;
		color: var(--color-text);
	}

	.review-header p {
		margin: 0.75rem 0 0;
		color: var(--color-text-muted);
	}

	.eyebrow {
		margin: 0;
		color: var(--color-accent);
		font-size: 0.78rem;
		font-weight: 750;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}

	.state-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 0.5rem;
	}

	.state-pill {
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		color: var(--color-text-muted);
		padding: 0.3rem 0.6rem;
		font-size: 0.75rem;
		font-weight: 750;
	}

	.state-pill.runnable {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
	}

	.spec-list {
		border-top: 1px solid var(--color-border-subtle);
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.spec-list div {
		display: grid;
		grid-template-columns: minmax(7rem, 0.8fr) minmax(0, 1.2fr);
		gap: 1rem;
		padding: 0.8rem 0;
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.spec-list div:last-child {
		border-bottom: 0;
	}

	.spec-list span {
		color: var(--color-text-muted);
	}

	.spec-list strong {
		color: var(--color-text);
		text-align: right;
	}

	.edit-plan {
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		padding: 0.85rem 1rem;
	}

	.edit-plan summary {
		cursor: pointer;
		font-weight: 750;
		color: var(--color-text);
	}

	.plan-fields,
	.model-section {
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
		margin-top: 1rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		color: var(--color-text-muted);
		font-size: 0.82rem;
		font-weight: 700;
	}

	select {
		width: 100%;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-bg);
		color: var(--color-text);
		padding: 0.55rem 0.6rem;
		font: inherit;
	}

	.section-title {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		color: var(--color-text);
		font-weight: 750;
	}

	.section-title small,
	.muted {
		color: var(--color-text-muted);
		font-size: 0.82rem;
	}

	.model-list {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		max-height: 16rem;
		overflow: auto;
	}

	.model-option {
		flex-direction: row;
		align-items: flex-start;
		gap: 0.65rem;
		padding: 0.55rem;
		border-radius: 0.4rem;
		background: var(--color-surface-muted);
	}

	.model-option span {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}

	.model-option strong {
		color: var(--color-text);
	}

	.model-option small {
		color: var(--color-text-muted);
		font-size: 0.78rem;
	}

	.form-error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.88rem;
	}

	.form-error p {
		margin: 0.25rem 0;
	}

	.run-button {
		margin-top: auto;
		border: 0;
		border-radius: 0.45rem;
		background: var(--color-accent);
		color: white;
		padding: 0.95rem 1rem;
		font: inherit;
		font-weight: 800;
		cursor: pointer;
	}

	.run-button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}

	@media (max-width: 54rem) {
		.setup-workspace,
		.setup-workspace.has-plan {
			grid-template-columns: 1fr;
			height: auto;
		}

		.setup-chat {
			min-height: 36rem;
		}

		.spec-list div {
			grid-template-columns: 1fr;
			gap: 0.25rem;
		}

		.spec-list strong {
			text-align: left;
		}
	}
</style>
