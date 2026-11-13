import { failureMessage } from '$lib/api/core';
import {
	getModelsScoredWith,
	submitChatBenchmark,
	updateChatBenchmarkConfig,
	type BenchmarkRunSpec,
	type BenchmarkValidation,
	type Dataset,
	type Job,
	type JobParams,
	type ModelConfig,
	type FocusAreaValue,
	type GuardrailThresholds,
	type Region,
	type RompDefaults
} from '$lib/api';
import type { BenchmarkStore } from '$lib/benchmarks.svelte';
import { gridsMatch } from '$lib/source-coverage';

export type SharedParamValue = string | number | FocusAreaValue | null;

/** An earlier benchmark's setup, for a new one to begin from. */
export type BenchmarkStart = {
	/** The region it was filed under, if any. */
	regionId?: string | null;
	datasetId: string;
	modelIds: string[];
	sharedParams: Record<string, SharedParamValue>;
};
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

function focusAreaParam(value: unknown): FocusAreaValue | undefined {
	return value !== null && typeof value === 'object' && ('lat_min' in value || 'units' in value)
		? (value as FocusAreaValue)
		: undefined;
}

function booleanParam(value: unknown): boolean | undefined {
	return typeof value === 'boolean' ? value : undefined;
}

// The shared ROMP parameters under "How onset is detected".
const ONSET_PARAM_KEYS = [
	'wet_threshold',
	'wet_init',
	'wet_spell',
	'dry_spell',
	'dry_extent',
	'nc_mask',
	'thresh_file',
	'ref_model',
	'ref_model_dir'
] as const;

function hasValue(value: unknown): boolean {
	return value !== undefined && value !== null && value !== '';
}

// Number inputs write strings, so 1998 and '1998' are the same value.
function sameValue(a: unknown, b: unknown): boolean {
	if (typeof a === 'object' || typeof b === 'object')
		return JSON.stringify(a) === JSON.stringify(b);
	return String(a ?? '') === String(b ?? '');
}

function differsFromDefaults(
	current: Record<string, unknown>,
	defaults: Record<string, unknown>
): boolean {
	return Object.keys(current).some((key) => !sameValue(current[key], defaults[key]));
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
	guardrails = $state<GuardrailThresholds | null>(null);
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
	// Each selected model's overrides as first seeded, so changing the dataset
	// (which moves the clamped defaults) doesn't count as a user edit.
	private seededDefaults = $state<Record<string, Record<string, unknown>>>({});
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
	readonly availableModels = $derived(
		this.models.filter((model) =>
			gridsMatch(this.selectedDataset?.grid_step_deg, model.grid_step_deg)
		)
	);
	readonly selectedModels = $derived(
		this.models.filter((model) => this.selectedModelIds.includes(model.id))
	);
	readonly onsetCustomised = $derived(
		ONSET_PARAM_KEYS.some((key) => hasValue(this.sharedAdvancedParams[key]))
	);
	readonly yearsCustomised = $derived(
		this.selectedModelIds.some((id) =>
			differsFromDefaults(
				this.perModelOverrides[id] ?? {},
				this.seededDefaults[id] ?? this.defaultModelOverride(id)
			)
		)
	);
	readonly canRun = $derived(
		Boolean(
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
		if ((nextSpec.dataset_id ?? '') !== this.selectedDatasetId) {
			void this.loadModels(nextSpec.dataset_id ?? '');
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

	/**
	 * Begin from an earlier benchmark's setup. Observations and models that are no
	 * longer offered are left for the user to choose again.
	 */
	startFrom = async (start: BenchmarkStart) => {
		const known = <T extends { id: string }>(items: T[], id: string | null | undefined) =>
			id && items.some((item) => item.id === id) ? id : '';
		this.selectedRegionId = known(this.regions, start.regionId);
		this.selectedDatasetId = known(this.datasets, start.datasetId);
		this.selectedModelIds = start.modelIds;
		this.sharedAdvancedParams = { ...start.sharedParams };
		await this.loadModels(this.selectedDatasetId);
		this.dropUnavailableModels();
	};

	private markManualConfigDirty() {
		this.manualConfigDirty = true;
		this.validation = null;
	}

	/** The models offered are the ones with data where the chosen observations have it. */
	private async loadModels(datasetId: string) {
		const token = ++this.modelsRequestToken;
		if (!datasetId) {
			this.models = [];
			return;
		}
		const fetchedModels = await getModelsScoredWith(datasetId);
		if (token !== this.modelsRequestToken) return;
		this.models = fetchedModels;
		this.dropUnavailableModels();
		for (const modelId of this.selectedModelIds) {
			if (this.perModelOverrides[modelId]) continue;
			this.seedOverride(modelId, this.defaultModelOverride(modelId, fetchedModels));
		}
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
		this.seedOverride(id, this.defaultModelOverride(id));
		this.markManualConfigDirty();
	};

	private seedOverride(modelId: string, defaults: Record<string, unknown>) {
		this.seededDefaults = { ...this.seededDefaults, [modelId]: defaults };
		this.perModelOverrides = {
			...this.perModelOverrides,
			[modelId]: defaults as ModelOverrides[string]
		};
	}

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

	// The region is a label for where the run is scored; an area picked for one
	// region means nothing in another.
	setRegionId = (id: string) => {
		if (id === this.selectedRegionId) return;
		this.selectedRegionId = id;
		this.sharedAdvancedParams = { ...this.sharedAdvancedParams, focus_area: null };
		this.markManualConfigDirty();
	};

	// Observations decide which models are offered, and the region the run starts in.
	// Models already picked stay picked where they still fit.
	setDatasetId = (id: string) => {
		if (id === this.selectedDatasetId) return;
		this.selectedDatasetId = id;
		this.setRegionId(this.datasets.find((item) => item.id === id)?.region ?? '');
		this.dropUnavailableModels();
		void this.loadModels(id);
		this.markManualConfigDirty();
	};

	private dropUnavailableModels() {
		const available = new Set(this.availableModels.map((model) => model.id));
		this.selectedModelIds = this.selectedModelIds.filter((id) => available.has(id));
		this.perModelOverrides = Object.fromEntries(
			Object.entries(this.perModelOverrides).filter(([id]) => available.has(id))
		);
	}

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
			// A cleared box must reach the server as null, or the merge keeps the old one.
			...('focus_area' in params && { focus_area: focusAreaParam(params.focus_area) ?? null }),
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
			if (showErrors) this.error = failureMessage(e);
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
						...(this.selectedRegionId && { region: this.selectedRegionId }),
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
			this.error = failureMessage(e);
			this.submitting = false;
		}
	};

	handleBenchmarkSubmitted = (runId: string, jobs: Job[], sessionId: string | null) => {
		this.submitting = false;
		this.getStore().acceptSubmittedJobs(runId, jobs);
		this.onSubmitted(runId, sessionId);
	};
}
