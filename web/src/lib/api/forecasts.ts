// ---- Forecasts ---------------------------------------------------------------
import { BASE_URL, request } from './core';
import { fetchResultBlob, type JobArtifact, type JobStatus } from './jobs';

export type Forecast = {
	id: string;
	blend_id: string;
	status: JobStatus;
	forecast_model_ids: string[];
	init_time: string | null;
	region_id?: string | null;
	created_at: string;
	completed_at?: string | null;
	error?: string | null;
	is_owner?: boolean;
	visibility?: 'private' | 'shared';
	run_id?: string | null;
};

export type ForecastParams = {
	init_time?: string | null;
	// Smoke-test knobs for the season-long blend-scoring loop: shrink it to a
	// short window instead of the full monsoon season. Unset means full season.
	max_lead_day?: number | null;
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
	resolution: string;
	description: string;
};

export async function listForecasts(): Promise<Forecast[]> {
	return request<Forecast[]>('/forecasts');
}

export async function createForecast(body: ForecastCreate): Promise<Forecast> {
	return request<Forecast>('/forecasts', { method: 'POST', body: JSON.stringify(body) });
}

export async function getForecastModels(): Promise<ForecastModel[]> {
	return request<ForecastModel[]>('/forecasts/models');
}

// One model's rendered map deliverable — a COG per (variable, lead hour),
// written by the job as `{model_id}/manifest.json` and indexed as a regular
// job artifact. Read client-side (small JSON) rather than adding a dedicated
// endpoint.
export type ForecastMapProduct = {
	unit: string;
	crs: string;
	cog: string;
	bounds_lonlat: [number, number, number, number];
	min: number;
	max: number;
};

export type ForecastManifest = {
	model_id: string;
	model_name: string;
	init_time: string | null;
	native_step_hours: number | null;
	variables: string[];
	lead_hours: number[];
	data_source: string;
	created_at: string;
	map_products: Record<string, Record<string, ForecastMapProduct>>;
};

export async function getForecastManifest(
	modelId: string,
	artifacts: JobArtifact[]
): Promise<ForecastManifest | null> {
	const artifact = artifacts.find((a) => a.filename === `${modelId}/manifest.json`);
	if (!artifact) return null;
	const objectUrl = await fetchResultBlob(artifact.url);
	const res = await fetch(objectUrl);
	return (await res.json()) as ForecastManifest;
}

// Tile/point URLs for the `/cog` router (TiTiler wrapping a job-scoped COG).
// `job_id`+`path` (not a raw URL) are validated server-side the same way
// `/jobs/{id}` endpoints check ownership.
export function cogTileTemplate(
	jobId: string,
	path: string,
	rescale: [number, number],
	colormapName = 'almanac'
): string {
	const qs = new URLSearchParams({
		job_id: jobId,
		path,
		rescale: `${rescale[0]},${rescale[1]}`,
		colormap_name: colormapName,
		return_mask: 'true'
	});
	return `${BASE_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?${qs.toString()}`;
}

export function cogPointUrl(jobId: string, path: string, lon: number, lat: number): string {
	const qs = new URLSearchParams({ job_id: jobId, path });
	return `${BASE_URL}/cog/point/${lon.toFixed(5)},${lat.toFixed(5)}?${qs.toString()}`;
}

// probs[date_idx] = [cv_week1, cv_week2, cv_week3, cv_week4, cv_later]
export type BlendForecastPoint = { lat: number; lon: number; probs: number[][] };

export type BlendForecastData = {
	issue_dates: string[];
	points: BlendForecastPoint[];
};

export async function getBlendForecast(jobId: string): Promise<BlendForecastData> {
	return request<BlendForecastData>(`/jobs/${jobId}/blend-forecast`);
}
