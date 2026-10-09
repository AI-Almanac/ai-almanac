// ---- Forecasts ---------------------------------------------------------------
import type { operations } from '../api-types.gen';
import { request } from './core';
import { type JobStatus } from './jobs';

export type Forecast = {
	id: string;
	blend_id: string;
	status: JobStatus;
	forecast_model_ids: string[];
	init_time: string | null;
	init_source?: string;
	region_id?: string | null;
	created_at: string;
	completed_at?: string | null;
	error?: string | null;
	is_owner?: boolean;
	visibility?: 'private' | 'shared' | 'example';
	run_id?: string | null;
};

export type ForecastParams = {
	init_time?: string | null;
	// Which archived analysis a live rollout initializes from (part of the
	// trajectory's identity). Defaults to "gfs" server-side.
	init_source?: string | null;
	// Smoke-test knob: shrink the season-long scoring loop to the most recent N
	// issue dates. Unset means the whole season-to-date.
	max_issue_dates?: number | null;
};

export type ForecastCreate = {
	blend_id: string;
	forecast_model_ids?: string[];
	params?: ForecastParams;
};

export type ForecastModel = {
	id: string;
	display_name: string;
	resolution_deg: number;
	description: string;
};

export async function listForecasts(): Promise<Forecast[]> {
	return request<Forecast[]>('/forecasts');
}

export async function createForecast(body: ForecastCreate): Promise<Forecast> {
	return request<Forecast>('/forecasts', { method: 'POST', body: JSON.stringify(body) });
}

// Re-run an existing forecast with its original parameters, so the cumulative
// season rollout reuses the cache and only fills in newly-elapsed init dates.
export async function refreshForecast(forecastId: string): Promise<Forecast> {
	return request<Forecast>(`/forecasts/${forecastId}/refresh`, { method: 'POST' });
}

export async function getForecastModels(): Promise<ForecastModel[]> {
	return request<ForecastModel[]>('/forecasts/models');
}

// Initialization data source a live rollout starts from (e.g. GFS, ERA5).
export type InitSource = {
	id: string;
	display_name: string;
};

export async function getInitSources(): Promise<InitSource[]> {
	return request<InitSource[]>('/forecasts/init-sources');
}

// A trajectory set: the deterministic season rollout for one
// (model_name, init_source, season) triple, shared across every blend/region
// that uses the model. Backs the admin coverage view.
export type TrajectorySet = {
	id: string;
	model_name: string;
	init_source: string | null;
	season: string | null;
	status: string;
	covered_init_dates: string[] | null;
	storage_prefix?: string | null;
	created_at: string;
	started_at?: string | null;
	completed_at?: string | null;
	error?: string | null;
};

export async function getTrajectorySets(): Promise<TrajectorySet[]> {
	return request<TrajectorySet[]>('/forecasts/trajectories');
}

// Which blend's probabilities a forecast view shows, and how they are binned.
// Bound to the generated query parameters so a backend rename is a TS error.
type BlendForecastQuery = NonNullable<
	operations['get_blend_forecast_jobs__job_id__blend_forecast_get']['parameters']['query']
>;
export type BlendForecastModel = NonNullable<BlendForecastQuery['model']>;
export type BlendForecastResolution = NonNullable<BlendForecastQuery['resolution']>;
export type BlendForecastView = { model: BlendForecastModel; resolution: BlendForecastResolution };

export const DEFAULT_BLEND_FORECAST_VIEW: BlendForecastView = {
	model: 'weekly_model',
	resolution: 'weekly'
};

// probs[date_idx] holds one probability per onset bin: Week 1..4 + Later for the
// weekly resolution, Day 1..28 + Later for daily (see OnsetBins in $lib/onset).
export type BlendForecastPoint = { id?: string; lat: number; lon: number; probs: number[][] };

export type BlendForecastData = {
	issue_dates: string[];
	points: BlendForecastPoint[];
	// Rainfall threshold (mm) that defines "onset"; null if the CSV omits it.
	onset_threshold: number | null;
	region_id: string | null;
	// Region display name and its onset definition (e.g. India → Modified
	// Moron–Robertson), so the UI can name what "onset" means here.
	region_name: string | null;
	onset_definition: string | null;
	// Views this forecast's outputs can serve; older forecasts offer only the default.
	available_views: BlendForecastView[];
};

export async function getBlendForecast(
	jobId: string,
	view: BlendForecastView = DEFAULT_BLEND_FORECAST_VIEW
): Promise<BlendForecastData> {
	const q = new URLSearchParams(view);
	return request<BlendForecastData>(`/jobs/${jobId}/blend-forecast?${q}`);
}
