// ---- Datasets ----------------------------------------------------------------
import { request } from './core';

export type Dataset = {
	id: string;
	name: string;
	status: string;
	region?: string | null;
	is_demo: boolean;
	created_at: string;
	obs_file_pattern?: string | null;
	obs_year_start?: number | null;
	obs_year_end?: number | null;
	provider?: string | null;
};

export async function getDatasets() {
	return request<Dataset[]>('/datasets');
}

export async function createDatasetFromPath(name: string, obs_dir: string) {
	return request<Dataset>('/datasets/from-path', {
		method: 'POST',
		body: JSON.stringify({ name, obs_dir })
	});
}
