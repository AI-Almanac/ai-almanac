import type {
	JobMetrics,
	MetricStats,
	ProvenanceStep,
	WorkspaceArtifact,
	WorkspaceJob
} from '../../src/lib/api';

/** A finished AIFS benchmark job, standing alone until a run id is given. */
export function workspaceJob(overrides: Partial<WorkspaceJob> = {}): WorkspaceJob {
	return {
		id: 'job-1',
		job_type: 'benchmark',
		status: 'complete',
		dataset_id: 'ds',
		region_id: null,
		blended: false,
		run_id: null,
		model_name: 'aifs',
		created_at: '2026-09-22T00:00:00Z',
		started_at: null,
		completed_at: null,
		error: null,
		area_of_interest: null,
		onset_definition: null,
		expected_seconds: null,
		scope: { place: 'Ethiopia Kiremt onset', years: null, lead_days: null },
		...overrides
	};
}

/** A figure no run produced, labelled and titled by its id. */
export function workspaceArtifact(overrides: Partial<WorkspaceArtifact> = {}): WorkspaceArtifact {
	const id = overrides.id ?? 'art-1';
	return {
		id,
		label: id,
		title: id,
		kind: 'figure',
		format: 'png',
		group: '',
		job_id: null,
		step_id: null,
		turn_id: null,
		run_job_ids: [],
		created_at: '2026-09-22T00:00:00Z',
		breadcrumb: [],
		summary: {},
		supporting: false,
		...overrides
	};
}

function stat(mean: number, unit: string): MetricStats {
	return { mean, min: 0, max: 0, p25: 0, p50: 0, p75: 0, p90: 0, unit };
}

/**
 * One job's scores: the mean onset error of `model`, and of the climatology
 * baseline when one is given, for each lead window.
 */
export function metricsFor(
	jobId: string,
	model: string,
	errors: Record<string, number>,
	climatology: Record<string, number> = {}
): JobMetrics {
	const rows = (who: string, byWindow: Record<string, number>) =>
		Object.entries(byWindow).map(([window, mean]) => ({
			model: who,
			window,
			tolerance_days: null,
			metrics: { mean_mae: stat(mean, 'days') }
		}));
	return {
		job_id: jobId,
		grid: null,
		bbox: null,
		windows: [...rows(model, errors), ...rows('climatology', climatology)]
	};
}

/** A step the assistant ran and the record still matches. */
export function provenanceStep(overrides: Partial<ProvenanceStep> = {}): ProvenanceStep {
	return {
		id: 's1',
		created_at: '2026-09-22T00:00:00Z',
		completed_at: null,
		actor_kind: 'assistant',
		primitive: 'benchmark.job',
		primitive_version: '1.0.0',
		description: '',
		params: {},
		display_params: {},
		inputs: [],
		outputs: [],
		runtime: {},
		job_id: null,
		code_hash: null,
		turn_id: null,
		session_id: null,
		ruleset_id: null,
		ruleset_version: null,
		model_name: null,
		approval_id: null,
		approved_at: null,
		parent_step_ids: [],
		step_hash: 'h',
		verification: 'verified',
		...overrides
	};
}
