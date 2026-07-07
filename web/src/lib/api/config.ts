// ---- App config: capabilities, metric definitions, ROMP defaults -------------
import { request } from './core';

export type MetricDefinition = {
	id: string;
	label: string;
	abbreviation: string;
	unit: string | null;
	range?: [number, number];
	lower_is_better?: boolean;
	description: string;
};

export type RompDefaults = {
	obs: string;
	obs_file_pattern: string;
	obs_var: string;
	model_var: string;
	file_pattern: string;
	region: string;
	nc_mask: string | null;
	thresh_file: string | null;
	wet_threshold: number;
	wet_init: number;
	wet_spell: number;
	dry_spell: number;
	dry_extent: number;
	start_date: string;
	end_date: string;
	start_year_clim: number;
	end_year_clim: number;
	max_forecast_day: number;
	probabilistic: boolean;
	members: string;
	parallel: boolean;
	ref_model: string;
	ref_model_dir: string | null;
	init_days: string;
	date_filter_year: number | null;
};

export type AppCapabilities = {
	chat: boolean;
};

export async function getMetricDefinitions() {
	return request<MetricDefinition[]>('/config/metrics');
}

export async function getRompDefaults() {
	return request<RompDefaults>('/config/romp-defaults');
}

export async function getCapabilities(): Promise<AppCapabilities> {
	return request<AppCapabilities>('/config/capabilities');
}
