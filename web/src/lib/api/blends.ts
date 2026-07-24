// ---- Blends ------------------------------------------------------------------
import { request } from './core';
import type { JobStatus } from './jobs';

export type Blend = {
	id: string;
	name: string;
	status: JobStatus;
	model_names: string[];
	region_id?: string | null;
	created_at: string;
	completed_at?: string | null;
	error?: string | null;
	is_owner?: boolean;
	visibility?: 'private' | 'shared';
	run_id?: string | null;
};

export type BlendParams = {
	training_years: string;
	cv_holdout_years: string;
	forecast_years?: string;
	obs_years?: string;
	true_holdout_years?: string;
	formula_text?: string;
	threshold_mm?: number;
	cutoff_month_day?: string;
	mok_month_day?: string;
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
