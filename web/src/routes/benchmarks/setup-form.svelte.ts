import {
	getModels,
	submitChatBenchmark,
	updateChatBenchmarkConfig,
	type BenchmarkRunSpec,
	type BenchmarkValidation,
	type Dataset,
	type Job,
	type JobParams,
	type ModelConfig,
	type Region,
	type RompDefaults
} from '$lib/api';
import type { BenchmarkStore } from '$lib/benchmarks.svelte';

type SharedParamValue = string | number | null;
type ModelOverrideValue = string | boolean | number;
type ModelOverrides = Record<string, Record<string, ModelOverrideValue>>;

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

/**
 * Owns benchmark-setup state for the manual form and the chat-assisted flow.
 * Components read fields and call the action methods; all selection and
 * validation state lives here rather than being threaded through props.
 */
export class BenchmarkSetupForm {
	regions = $state<Region[]>([]);
	datasets = $state<Dataset[]>([]);
	parameterDefaults = $state<RompDefaults | null>(null);
	dataLoaded = $state(false);

	spec = $state<BenchmarkRunSpec | null>(null);
	validation = $state<BenchmarkValidation | null>(null);
	models = $state<ModelConfig[]>([]);
	selectedRegionId = $state('');
	selectedDatasetId = $state('');
	selectedModelIds = $state<string[]>([]);
	forecastWindowDays = $state<number | null>(30);
	sharedAdvancedParams = $state<Record<string, SharedParamValue>>({});
	perModelOverrides = $state<ModelOverrides>({});
	submitting = $state(false);
	syncingConfig = $state(false);
	manualConfigDirty = $state(false);
	error = $state<string | null>(null);
	chatSessionId = $state<string | null>(null);

	readonly selectedRegion = $derived(
		this.regions.find((region) => region.id === this.selectedRegionId) ?? null
	);
	readonly selectedDataset = $derived(
		this.datasets.find((dataset) => dataset.id === this.selectedDatasetId) ?? null
	);
	readonly selectedModels = $derived(
		this.models.filter((model) => this.selectedModelIds.includes(model.id))
	);
	readonly canRun = $derived(
		Boolean(
			this.selectedRegionId &&
			this.selectedDatasetId &&
			this.selectedModelIds.length > 0 &&
			!(this.validation?.errors.length ?? 0)
		)
	);
	readonly runState = $derived(
		this.submitting ? 'running' : this.canRun ? 'runnable' : (this.spec?.status ?? 'collecting')
	);

	private modelsRequestToken = 0;

	constructor(
		private getStore: () => BenchmarkStore,
		private onSubmitted: (runId: string, chatSessionId: string | null) => void
	) {}

	applySpec = (nextSpec: BenchmarkRunSpec, nextValidation?: BenchmarkValidation | null) => {
		this.spec = nextSpec;
		this.validation = nextValidation ?? this.validation;
		if ((nextSpec.region_id ?? '') !== this.selectedRegionId) {
			void this.loadModels(nextSpec.region_id ?? '');
		}
		this.selectedRegionId = nextSpec.region_id ?? '';
		this.selectedDatasetId = nextSpec.dataset_id ?? '';
		this.selectedModelIds = nextSpec.model_ids;
		this.forecastWindowDays = nextSpec.forecast_window_days ?? null;
		const advanced = nextSpec.advanced_params ?? {};
		this.sharedAdvancedParams = Object.fromEntries(
			Object.entries(advanced).filter(([key]) => key !== 'per_model_params')
		) as Record<string, SharedParamValue>;
		this.perModelOverrides = (advanced.per_model_params as ModelOverrides | null) ?? {};
	};

	private markManualConfigDirty() {
		this.manualConfigDirty = true;
		this.validation = null;
	}

	private async loadModels(regionId: string) {
		const token = ++this.modelsRequestToken;
		if (!regionId) {
			this.models = [];
			return;
		}
		const fetchedModels = await getModels(regionId);
		if (token !== this.modelsRequestToken) return;
		this.models = fetchedModels;
		this.perModelOverrides = this.selectedModelIds.reduce(
			(overrides, modelId) => ({
				...overrides,
				[modelId]: overrides[modelId] ?? this.defaultModelOverride(modelId, fetchedModels)
			}),
			this.perModelOverrides
		);
	}

	toggleModel = (id: string) => {
		if (this.selectedModelIds.includes(id)) {
			this.selectedModelIds = this.selectedModelIds.filter((modelId) => modelId !== id);
			const { [id]: _removed, ...remaining } = this.perModelOverrides;
			this.perModelOverrides = remaining;
			this.markManualConfigDirty();
			return;
		}
		this.selectedModelIds = [...this.selectedModelIds, id];
		this.perModelOverrides = {
			...this.perModelOverrides,
			[id]: this.defaultModelOverride(id)
		};
		this.markManualConfigDirty();
	};

	private defaultModelOverride(modelId: string, modelList = this.models) {
		const cfg = modelList.find((model) => model.id === modelId);
		if (!cfg) return {};
		const obsStart = this.selectedDataset?.obs_year_start ?? null;
		const obsEnd = this.selectedDataset?.obs_year_end ?? null;
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

	setSharedParam = (key: string, value: SharedParamValue) => {
		this.sharedAdvancedParams = { ...this.sharedAdvancedParams, [key]: value };
		this.markManualConfigDirty();
	};

	setOverride = (modelId: string, key: string, value: ModelOverrideValue) => {
		this.perModelOverrides = {
			...this.perModelOverrides,
			[modelId]: { ...(this.perModelOverrides[modelId] ?? {}), [key]: value }
		};
		this.markManualConfigDirty();
	};

	getOverride = <T>(modelId: string, key: string, fallback: T): T => {
		const value = this.perModelOverrides[modelId]?.[key];
		return value !== undefined ? (value as T) : fallback;
	};

	setRegionId = (id: string) => {
		this.selectedRegionId = id;
		this.selectedModelIds = [];
		this.perModelOverrides = {};
		this.models = [];
		void this.loadModels(id);
		this.markManualConfigDirty();
	};

	setDatasetId = (id: string) => {
		this.selectedDatasetId = id;
		const dataset = this.datasets.find((item) => item.id === id);
		if (dataset?.region && dataset.region !== this.selectedRegionId) {
			this.setRegionId(dataset.region);
			return;
		}
		this.markManualConfigDirty();
	};

	setForecastWindowDays = (days: number | null) => {
		this.forecastWindowDays = days;
		this.markManualConfigDirty();
	};

	private sharedAdvancedPatch(): Partial<JobParams> {
		const params = this.sharedAdvancedParams;
		return {
			...(stringParam(params.obs) && { obs: stringParam(params.obs) }),
			...(stringParam(params.obs_file_pattern) && {
				obs_file_pattern: stringParam(params.obs_file_pattern)
			}),
			...(stringParam(params.obs_var) && { obs_var: stringParam(params.obs_var) }),
			...(numberParam(params.wet_threshold) !== undefined && {
				wet_threshold: numberParam(params.wet_threshold)
			}),
			...(numberParam(params.wet_init) !== undefined && { wet_init: numberParam(params.wet_init) }),
			...(numberParam(params.wet_spell) !== undefined && {
				wet_spell: numberParam(params.wet_spell)
			}),
			...(numberParam(params.dry_spell) !== undefined && {
				dry_spell: numberParam(params.dry_spell)
			}),
			...(numberParam(params.dry_extent) !== undefined && {
				dry_extent: numberParam(params.dry_extent)
			}),
			...(stringParam(params.nc_mask) && { nc_mask: stringParam(params.nc_mask) }),
			...(stringParam(params.thresh_file) && { thresh_file: stringParam(params.thresh_file) }),
			...(stringParam(params.ref_model) && { ref_model: stringParam(params.ref_model) }),
			...(stringParam(params.ref_model_dir) && {
				ref_model_dir: stringParam(params.ref_model_dir)
			})
		};
	}

	private perModelPatch() {
		const overrides: Record<string, Partial<JobParams>> = {};
		for (const modelId of this.selectedModelIds) {
			const raw = this.perModelOverrides[modelId] ?? {};
			const probabilistic = booleanParam(raw.probabilistic);
			overrides[modelId] = {
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
		}
		return overrides;
	}

	private configPatch(): Partial<BenchmarkRunSpec> {
		const modelParams = this.perModelPatch();
		return {
			intent: this.spec?.intent ?? '',
			region_id: this.selectedRegionId || null,
			dataset_id: this.selectedDatasetId || null,
			model_ids: this.selectedModelIds,
			event_type: this.spec?.event_type ?? 'monsoon_onset',
			forecast_window_days: this.forecastWindowDays,
			advanced_params: {
				...this.sharedAdvancedPatch(),
				...(Object.keys(modelParams).length > 0 && { per_model_params: modelParams })
			}
		};
	}

	syncBenchmarkConfig = async ({ showErrors = true } = {}) => {
		if (!this.chatSessionId) return null;
		this.syncingConfig = true;
		if (showErrors) this.error = null;
		try {
			const updated = await updateChatBenchmarkConfig(this.chatSessionId, this.configPatch());
			this.applySpec(updated.benchmark_config, updated.benchmark_validation);
			this.manualConfigDirty = false;
			if (showErrors && updated.benchmark_validation.errors.length > 0) {
				this.error = updated.benchmark_validation.errors[0];
			}
			return updated;
		} catch (e) {
			if (showErrors) this.error = (e as Error).message ?? 'Benchmark config validation failed.';
			return null;
		} finally {
			this.syncingConfig = false;
		}
	};

	handleSessionReady = (id: string) => {
		this.chatSessionId = id;
		if (this.manualConfigDirty) void this.syncBenchmarkConfig({ showErrors: false });
	};

	runBenchmark = async () => {
		if (!this.canRun) {
			this.error = 'The benchmark plan is missing required fields.';
			return;
		}
		this.submitting = true;
		this.error = null;
		try {
			if (!this.chatSessionId) {
				const result = await this.getStore().submitRuns({
					datasetId: this.selectedDatasetId,
					modelNames: this.selectedModelIds,
					sharedParams: {
						region: this.selectedRegionId,
						event_type: this.spec?.event_type ?? 'monsoon_onset',
						...(this.forecastWindowDays !== null && {
							max_forecast_day: this.forecastWindowDays
						}),
						...this.sharedAdvancedPatch()
					},
					perModelOverrides: this.perModelPatch()
				});
				this.handleBenchmarkSubmitted(result.runId, result.jobs, null);
				return;
			}
			const updated = await this.syncBenchmarkConfig({ showErrors: true });
			if (!updated) {
				this.submitting = false;
				return;
			}
			if (!updated.benchmark_validation.can_run) {
				this.error =
					updated.benchmark_validation.errors[0] ??
					'The benchmark plan is missing required fields.';
				this.submitting = false;
				return;
			}
			const response = await submitChatBenchmark(this.chatSessionId);
			this.applySpec(response.benchmark_config, response.benchmark_validation);
			this.handleBenchmarkSubmitted(response.run_id, response.jobs, this.chatSessionId);
		} catch (e) {
			this.error = (e as Error).message ?? 'Benchmark submit failed.';
			this.submitting = false;
		}
	};

	handleBenchmarkSubmitted = (runId: string, jobs: Job[], sessionId: string | null) => {
		this.submitting = false;
		this.getStore().acceptSubmittedJobs(runId, jobs);
		this.onSubmitted(runId, sessionId);
	};
}
