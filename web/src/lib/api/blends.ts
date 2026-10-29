// ---- Blends ------------------------------------------------------------------
import { request } from './core';
import type { FocusAreaValue, JobStatus } from './jobs';

export type Blend = {
	id: string;
	name: string;
	status: JobStatus;
	model_names: string[];
	// Member name → live forecast model id, for members whose data is linked to one.
	forecast_models?: Record<string, string>;
	// Years kept out of every fit, scored apart from the cross-validation years.
	true_holdout_years?: number[];
	region_id?: string | null;
	created_at: string;
	completed_at?: string | null;
	error?: string | null;
	is_owner?: boolean;
	visibility?: 'private' | 'shared' | 'example';
	run_id?: string | null;
	// The day-level blend when it trains in its own run alongside this one, so
	// a complete blend can still be 'running' here. Absent when it trains with
	// the blend or not at all.
	day_level_status?: DayLevelStatus | null;
	day_level_error?: string | null;
};

export type DayLevelStatus = 'running' | 'complete' | 'failed' | 'canceled';

export type BlendParams = {
	training_years: string;
	cv_holdout_years: string;
	forecast_years?: string;
	obs_years?: string;
	true_holdout_years?: string;
	formula_text?: string;
	// Onset definition; omitted, the region's (modified Moron–Robertson in India,
	// ICPAC elsewhere), and omitted fields use its defaults.
	onset_definition?: 'icpac' | 'moron_robertson_mok';
	threshold_mm?: number;
	cutoff_month_day?: string;
	ref_onset_month_day?: string;
	focus_area?: FocusAreaValue;
	// The day-level blend's CV and forest settings; omitted ones keep its defaults.
	day_level?: DayLevelSettings;
};

// The day-level blend's CV and forest settings (server DayLevelSettings).
export type DayLevelSettings = {
	years_per_fold?: number;
	trees?: number;
	max_depth?: number;
	min_samples_leaf?: number;
	max_samples?: number;
};

export type BlendCreate = {
	name: string;
	obs_dataset_id: string;
	model_ids: string[];
	params: BlendParams;
	run_id?: string;
};

export async function listBlends(): Promise<Blend[]> {
	return request<Blend[]>('/blends');
}

export async function createBlend(body: BlendCreate): Promise<Blend> {
	return request<Blend>('/blends', { method: 'POST', body: JSON.stringify(body) });
}
