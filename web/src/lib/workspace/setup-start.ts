/** Where a new run started from the workspace begins: an earlier run, a draft, or nothing. */
import type { Job } from '$lib/api/jobs';
import type { WorkspaceJob } from '$lib/api/workspaces';
import type {
	BenchmarkStart,
	SharedParamValue
} from '$lib/components/benchmark-setup/setup-form.svelte';
import type { BlendRunSpec } from '$lib/api/chat';
import type { BenchmarkDraft, BlendDraft } from '$lib/interface-actions/actions';
import { groupByRun, jobKind, runKind, type RunKind } from './runs';

/** The workspace's most recent finished blend, which a new forecast starts from; null without one. */
export function latestBlendId(jobs: WorkspaceJob[]): string | null {
	const finished = jobs.filter((job) => jobKind(job) === 'blend' && job.status === 'complete');
	const newest = finished.reduce<WorkspaceJob | null>(
		(latest, job) => (latest && latest.created_at >= job.created_at ? latest : job),
		null
	);
	return newest?.id ?? null;
}

/** The jobs of the workspace's most recent benchmark; none when it has no benchmark. */
export function latestBenchmarkJobIds(jobs: WorkspaceJob[]): string[] {
	const latest = groupByRun(jobs, []).runs.find((run) => runKind(run) === 'benchmark');
	return latest ? latest.jobs.map((job) => job.id) : [];
}

// What makes up an onset definition among a benchmark's settings.
const ONSET_DEFINITION_KEYS = [
	'wet_threshold',
	'wet_init',
	'wet_spell',
	'dry_spell',
	'dry_extent'
] as const;

function carriedSettings(params: Job['params']): Record<string, SharedParamValue> {
	const carried: Record<string, SharedParamValue> = {};
	if (params?.focus_area) carried.focus_area = params.focus_area;
	for (const key of ONSET_DEFINITION_KEYS) {
		const value = params?.[key];
		if (value !== undefined && value !== null) carried[key] = value;
	}
	return carried;
}

/**
 * The model as the setup form lists it: by the forecast data it was scored from. A
 * job's own model name is what the scoring run called it, which the form never offers.
 */
function formModelId(job: Job): string {
	return job.model_source_id ?? job.model_name;
}

/**
 * What a new benchmark begins from, given the jobs of an earlier one: its region,
 * observations, models, area of interest and onset definition. Evaluation years
 * are left to the form's defaults.
 */
export function benchmarkStart(jobs: Job[]): BenchmarkStart | null {
	const first = jobs[0];
	if (!first) return null;
	return {
		regionId: first.region_id ?? first.params?.region ?? '',
		datasetId: first.dataset_id,
		modelIds: [...new Set(jobs.map(formModelId))],
		sharedParams: carriedSettings(first.params)
	};
}

/**
 * What a new benchmark begins from when something supplied a draft. A draft that
 * names observations but no region takes the region those observations are for.
 */
export function benchmarkStartFromDraft(
	draft: BenchmarkDraft,
	datasets: { id: string; region?: string | null }[]
): BenchmarkStart {
	const dataset = datasets.find((candidate) => candidate.id === draft.dataset_id);
	return {
		regionId: draft.region_id ?? dataset?.region ?? '',
		datasetId: draft.dataset_id ?? '',
		modelIds: draft.model_ids,
		sharedParams: draft.focus_area ? { focus_area: draft.focus_area } : {}
	};
}

/** A draft as the blend form takes a setup: every field present, blank where the draft is silent. */
export function blendSetupFromDraft(draft: BlendDraft): BlendRunSpec {
	return {
		intent: '',
		name: draft.name ?? '',
		obs_dataset_id: draft.obs_dataset_id ?? null,
		model_ids: draft.model_ids,
		model_names: [],
		training_years: draft.training_years ?? '',
		cv_holdout_years: draft.cv_holdout_years ?? '',
		forecast_years: draft.forecast_years ?? '',
		true_holdout_years: draft.true_holdout_years ?? '',
		formula_text: draft.formula_text ?? '',
		focus_area: draft.focus_area ?? null,
		threshold_mm: draft.threshold_mm ?? null,
		cutoff_month_day: draft.cutoff_month_day ?? '',
		ref_onset_month_day: draft.ref_onset_month_day ?? '',
		status: 'collecting',
		missing_fields: [],
		assumptions: []
	};
}

/** What the setup panel says when a run started but could not be put into the workspace. */
export function attachFailure(kind: RunKind): string {
	return `The ${kind} started, but it could not be added to this workspace. Add it with New → Add an existing run.`;
}
