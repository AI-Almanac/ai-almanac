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
		type JobParams,
		type Job,
		type ModelConfig,
		type Region,
		type RompDefaults
	} from '$lib/api';
	import AdvancedRompConfigPanel from './AdvancedRompConfigPanel.svelte';

	let {
		store,
		regions,
		datasets,
		dataLoaded,
		parameterDefaults,
		initialPrompt = '',
		initialManualOpen = false,
		onSubmitted
	}: {
		store: BenchmarkStore;
		regions: Region[];
		datasets: Dataset[];
		dataLoaded: boolean;
		parameterDefaults: RompDefaults | null;
		initialPrompt?: string;
		initialManualOpen?: boolean;
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
	let sharedAdvancedParams = $state<Record<string, string | number | null>>({});
	let perModelOverrides = $state<Record<string, Record<string, string | boolean | number>>>({});
	let detailsOpen = $state(true);
	let advancedPanelOpen = $state(false);
	let initialManualOpenHandled = $state(false);
	let submitting = $state(false);
	let syncingConfig = $state(false);
	let manualConfigDirty = $state(false);
	let error = $state<string | null>(null);
	let chatSessionId = $state<string | null>(null);

	const selectedRegion = $derived(regions.find((region) => region.id === selectedRegionId) ?? null);
	const selectedDataset = $derived(
		datasets.find((dataset) => dataset.id === selectedDatasetId) ?? null
	);
	const selectedModels = $derived(models.filter((model) => selectedModelIds.includes(model.id)));
	const canRun = $derived(
		Boolean(
			selectedRegionId &&
			selectedDatasetId &&
			selectedModelIds.length > 0 &&
			!(validation?.errors.length ?? 0)
		)
	);
	const runState = $derived(
		submitting ? 'running' : canRun ? 'runnable' : (spec?.status ?? 'collecting')
	);
	const specSlots = $derived([
		{ label: 'Mode', value: spec?.intent ? 'Chat-assisted setup' : 'Manual setup' },
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
			perModelOverrides = selectedModelIds.reduce(
				(overrides, modelId) => ({
					...overrides,
					[modelId]: overrides[modelId] ?? defaultModelOverride(modelId, fetchedModels)
				}),
				perModelOverrides
			);
		});
	});

	$effect(() => {
		if (!initialManualOpen || initialManualOpenHandled) return;
		initialManualOpenHandled = true;
		advancedPanelOpen = true;
	});

	$effect(() => {
		if (!chatSessionId || !manualConfigDirty) return;
		const timer = setTimeout(() => {
			void syncBenchmarkConfig({ showErrors: false });
		}, 500);
		return () => clearTimeout(timer);
	});

	function applySpec(nextSpec: BenchmarkRunSpec, nextValidation?: BenchmarkValidation | null) {
		spec = nextSpec;
		validation = nextValidation ?? validation;
		selectedRegionId = nextSpec.region_id ?? '';
		selectedDatasetId = nextSpec.dataset_id ?? '';
		selectedModelIds = nextSpec.model_ids;
		forecastWindowDays = nextSpec.forecast_window_days ?? null;
		const advanced = nextSpec.advanced_params ?? {};
		sharedAdvancedParams = Object.fromEntries(
			Object.entries(advanced).filter(([key]) => key !== 'per_model_params')
		) as Record<string, string | number | null>;
		perModelOverrides =
			(advanced.per_model_params as Record<
				string,
				Record<string, string | boolean | number>
			> | null) ?? {};
		if (nextSpec.status === 'runnable') detailsOpen = true;
	}

	function markManualConfigDirty() {
		manualConfigDirty = true;
		validation = null;
	}

	function toggleModel(id: string) {
		if (selectedModelIds.includes(id)) {
			selectedModelIds = selectedModelIds.filter((modelId) => modelId !== id);
			const { [id]: _removed, ...remaining } = perModelOverrides;
			perModelOverrides = remaining;
			markManualConfigDirty();
			return;
		}
		selectedModelIds = [...selectedModelIds, id];
		perModelOverrides = {
			...perModelOverrides,
			[id]: defaultModelOverride(id, models)
		};
		markManualConfigDirty();
	}

	function defaultModelOverride(modelId: string, modelList = models) {
		const cfg = modelList.find((model) => model.id === modelId);
		if (!cfg) return {};
		const obsStart = selectedDataset?.obs_year_start ?? null;
		const obsEnd = selectedDataset?.obs_year_end ?? null;
		const clampYear = (year: number) => {
			let nextYear = year;
			if (obsStart !== null) nextYear = Math.max(nextYear, obsStart);
			if (obsEnd !== null) nextYear = Math.min(nextYear, obsEnd);
			return nextYear;
		};
		const clampDate = (date: string) => `${clampYear(Number(date.slice(0, 4)))}${date.slice(4)}`;
		return {
			start_date: clampDate(cfg.start_date),
			end_date: clampDate(cfg.end_date),
			start_year_clim: clampYear(cfg.start_year_clim),
			end_year_clim: clampYear(cfg.end_year_clim),
			init_days: cfg.init_days,
			...(cfg.date_filter_year != null && { date_filter_year: cfg.date_filter_year }),
			parallel: !cfg.probabilistic,
			probabilistic: cfg.probabilistic,
			members: cfg.members ?? '',
			model_var: cfg.model_var !== 'tp' ? cfg.model_var : '',
			file_pattern: cfg.file_pattern !== '{}.nc' ? cfg.file_pattern : ''
		};
	}

	function setSharedParam(key: string, value: string | number | null) {
		sharedAdvancedParams = { ...sharedAdvancedParams, [key]: value };
		markManualConfigDirty();
	}

	function setOverride(modelId: string, key: string, value: string | boolean | number) {
		perModelOverrides = {
			...perModelOverrides,
			[modelId]: { ...(perModelOverrides[modelId] ?? {}), [key]: value }
		};
		markManualConfigDirty();
	}

	function getOverride<T>(modelId: string, key: string, fallback: T): T {
		const value = perModelOverrides[modelId]?.[key];
		return value !== undefined ? (value as T) : fallback;
	}

	function setRegionId(id: string) {
		selectedRegionId = id;
		selectedDatasetId = '';
		selectedModelIds = [];
		perModelOverrides = {};
		models = [];
		markManualConfigDirty();
	}

	function setDatasetId(id: string) {
		selectedDatasetId = id;
		markManualConfigDirty();
	}

	function setForecastWindowDays(days: number | null) {
		forecastWindowDays = days;
		markManualConfigDirty();
	}

	function numberParam(value: unknown): number | undefined {
		if (value === null || value === undefined || value === '') return undefined;
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : undefined;
	}

	function stringParam(value: unknown): string | undefined {
		return typeof value === 'string' && value.trim() ? value.trim() : undefined;
	}

	function booleanParam(value: unknown): boolean | undefined {
		return typeof value === 'boolean' ? value : undefined;
	}

	function sharedAdvancedPatch(): Partial<JobParams> {
		return {
			...(stringParam(sharedAdvancedParams.obs) && { obs: stringParam(sharedAdvancedParams.obs) }),
			...(stringParam(sharedAdvancedParams.obs_file_pattern) && {
				obs_file_pattern: stringParam(sharedAdvancedParams.obs_file_pattern)
			}),
			...(stringParam(sharedAdvancedParams.obs_var) && {
				obs_var: stringParam(sharedAdvancedParams.obs_var)
			}),
			...(numberParam(sharedAdvancedParams.wet_threshold) !== undefined && {
				wet_threshold: numberParam(sharedAdvancedParams.wet_threshold)
			}),
			...(numberParam(sharedAdvancedParams.wet_init) !== undefined && {
				wet_init: numberParam(sharedAdvancedParams.wet_init)
			}),
			...(numberParam(sharedAdvancedParams.wet_spell) !== undefined && {
				wet_spell: numberParam(sharedAdvancedParams.wet_spell)
			}),
			...(numberParam(sharedAdvancedParams.dry_spell) !== undefined && {
				dry_spell: numberParam(sharedAdvancedParams.dry_spell)
			}),
			...(numberParam(sharedAdvancedParams.dry_extent) !== undefined && {
				dry_extent: numberParam(sharedAdvancedParams.dry_extent)
			}),
			...(stringParam(sharedAdvancedParams.nc_mask) && {
				nc_mask: stringParam(sharedAdvancedParams.nc_mask)
			}),
			...(stringParam(sharedAdvancedParams.thresh_file) && {
				thresh_file: stringParam(sharedAdvancedParams.thresh_file)
			}),
			...(stringParam(sharedAdvancedParams.ref_model) && {
				ref_model: stringParam(sharedAdvancedParams.ref_model)
			}),
			...(stringParam(sharedAdvancedParams.ref_model_dir) && {
				ref_model_dir: stringParam(sharedAdvancedParams.ref_model_dir)
			})
		};
	}

	function perModelPatch() {
		const overrides: Record<string, Partial<JobParams>> = {};
		for (const modelId of selectedModelIds) {
			const raw = perModelOverrides[modelId] ?? {};
			const probabilistic = booleanParam(raw.probabilistic);
			const params: Partial<JobParams> = {
				...(stringParam(raw.start_date) && { start_date: stringParam(raw.start_date) }),
				...(stringParam(raw.end_date) && { end_date: stringParam(raw.end_date) }),
				...(numberParam(raw.start_year_clim) !== undefined && {
					start_year_clim: numberParam(raw.start_year_clim)
				}),
				...(numberParam(raw.end_year_clim) !== undefined && {
					end_year_clim: numberParam(raw.end_year_clim)
				}),
				...(stringParam(raw.init_days) && { init_days: stringParam(raw.init_days) }),
				...(numberParam(raw.date_filter_year) !== undefined && {
					date_filter_year: numberParam(raw.date_filter_year)
				}),
				...(probabilistic !== undefined && { probabilistic }),
				parallel: probabilistic ? false : Boolean(raw.parallel ?? true),
				...(stringParam(raw.members) && { members: stringParam(raw.members) }),
				...(stringParam(raw.model_var) && { model_var: stringParam(raw.model_var) }),
				...(stringParam(raw.file_pattern) && { file_pattern: stringParam(raw.file_pattern) })
			};
			overrides[modelId] = params;
		}
		return overrides;
	}

	function configPatch(): Partial<BenchmarkRunSpec> {
		const modelParams = perModelPatch();
		return {
			intent: spec?.intent ?? '',
			region_id: selectedRegionId || null,
			dataset_id: selectedDatasetId || null,
			model_ids: selectedModelIds,
			event_type: spec?.event_type ?? 'monsoon_onset',
			forecast_window_days: forecastWindowDays,
			advanced_params: {
				...sharedAdvancedPatch(),
				...(Object.keys(modelParams).length > 0 && { per_model_params: modelParams })
			}
		};
	}

	async function syncBenchmarkConfig({ showErrors = true } = {}) {
		if (!chatSessionId) return null;
		syncingConfig = true;
		if (showErrors) error = null;
		try {
			const updated = await updateChatBenchmarkConfig(chatSessionId, configPatch());
			applySpec(updated.benchmark_config, updated.benchmark_validation);
			manualConfigDirty = false;
			if (showErrors && updated.benchmark_validation.errors.length > 0) {
				error = updated.benchmark_validation.errors[0];
			}
			return updated;
		} catch (e: any) {
			if (showErrors) error = e.message ?? 'Benchmark config validation failed.';
			return null;
		} finally {
			syncingConfig = false;
		}
	}

	function handleSessionReady(id: string) {
		chatSessionId = id;
		if (manualConfigDirty) void syncBenchmarkConfig({ showErrors: false });
	}

	function closeManualConfig() {
		advancedPanelOpen = false;
		void syncBenchmarkConfig({ showErrors: true });
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
			const updated = await syncBenchmarkConfig({ showErrors: true });
			if (!updated) {
				submitting = false;
				return;
			}
			if (!updated.benchmark_validation.can_run) {
				error =
					updated.benchmark_validation.errors[0] ??
					'The benchmark plan is missing required fields.';
				submitting = false;
				return;
			}
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

<section class="setup-workspace" class:has-plan={detailsOpen}>
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
			onSessionReady={handleSessionReady}
			onBenchmarkConfig={applySpec}
			onBenchmarkSubmitted={handleBenchmarkSubmitted}
		/>
	</div>

	{#if detailsOpen}
		<aside class="review-panel">
			<div class="review-header">
				<div class="state-row">
					<p class="eyebrow">Current setup</p>
					<span class="state-pill" class:runnable={runState === 'runnable'}>
						{runState === 'runnable'
							? 'Runnable'
							: runState === 'running'
								? 'Starting'
								: 'Needs info'}
					</span>
				</div>
				<h2>Benchmark plan</h2>
				<p>{selectedRegion?.display_name ?? spec?.region_name ?? 'No region selected'}</p>
			</div>

			<div class="spec-list">
				{#each specSlots as slot}
					<div>
						<span>{slot.label}</span>
						<strong>{slot.value}</strong>
					</div>
				{/each}
			</div>

			<button class="advanced-button" type="button" onclick={() => (advancedPanelOpen = true)}>
				<span>Manual configuration</span>
				<small>
					{#if syncingConfig}
						Validating...
					{:else}
						{selectedModelIds.length} selected model{selectedModelIds.length === 1 ? '' : 's'}
					{/if}
				</small>
			</button>

			{#if validation?.errors.length}
				<div class="form-error">
					{#each validation.errors as validationError}
						<p>{validationError}</p>
					{/each}
				</div>
			{:else if error}
				<p class="form-error">{error}</p>
			{/if}

			<button
				class="run-button"
				type="button"
				disabled={!canRun || submitting}
				onclick={runBenchmark}
			>
				{submitting ? 'Starting run...' : 'Run benchmark'}
			</button>
		</aside>
	{/if}
</section>

<AdvancedRompConfigPanel
	open={advancedPanelOpen}
	{regions}
	{datasets}
	{dataLoaded}
	{models}
	{selectedRegionId}
	{selectedDatasetId}
	{selectedModelIds}
	{forecastWindowDays}
	{selectedRegion}
	{selectedDataset}
	{sharedAdvancedParams}
	{parameterDefaults}
	{setRegionId}
	{setDatasetId}
	{setForecastWindowDays}
	{toggleModel}
	{setSharedParam}
	{getOverride}
	{setOverride}
	onClose={closeManualConfig}
/>

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

	.advanced-button {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		width: 100%;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
		padding: 0.85rem 1rem;
		font: inherit;
		color: var(--color-text);
		text-align: left;
		cursor: pointer;
	}

	.advanced-button span {
		color: var(--color-text);
		font-weight: 850;
	}

	.advanced-button small {
		color: var(--color-text-muted);
		font-weight: 750;
		white-space: nowrap;
	}

	.advanced-button:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.advanced-button:not(:disabled):hover {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}

	@media (max-width: 30rem) {
		.advanced-button {
			align-items: flex-start;
			flex-direction: column;
		}

		.advanced-button small {
			white-space: normal;
		}
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
