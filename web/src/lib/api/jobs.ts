// ---- Jobs: submission, streaming, results, metrics ----------------------------
import { authHeaders } from '../auth';
import { BASE_URL, request } from './core';

export type JobStatus =
	| 'queued'
	| 'starting'
	| 'running'
	| 'canceling'
	| 'canceled'
	| 'complete'
	| 'failed';

export type Job = {
	id: string;
	status: JobStatus;
	dataset_id: string;
	model_name: string;
	model_display_name: string;
	model_source_id?: string | null;
	model_dir?: string;
	obs_dir?: string;
	params?: JobParams;
	region_id?: string | null;
	region_name?: string | null;
	romp_region?: string | null;
	created_at?: string;
	started_at?: string;
	completed_at?: string;
	error?: string | null;
	is_owner?: boolean;
	visibility?: 'private' | 'shared';
	run_id?: string | null;
};

export type JobParams = {
	// Essential
	region?: string;
	start_date?: string;
	end_date?: string;
	event_type?: string;
	// Common
	start_year_clim?: number;
	end_year_clim?: number;
	max_forecast_day?: number;
	init_days?: string;
	date_filter_year?: number | null;
	parallel?: boolean;
	// Advanced — obs overrides
	obs?: string;
	obs_file_pattern?: string;
	obs_var?: string;
	model_var?: string;
	file_pattern?: string;
	// Advanced — wet/dry spell thresholds
	wet_threshold?: number;
	wet_init?: number;
	wet_spell?: number;
	dry_spell?: number;
	dry_extent?: number;
	// Advanced — probabilistic
	probabilistic?: boolean;
	members?: string;
	// Advanced — reference model
	ref_model?: string;
	ref_model_dir?: string;
	// Advanced — masks/thresholds
	nc_mask?: string;
	thresh_file?: string;
};

export type SubmitJobParams = {
	dataset_id: string;
	model_name: string;
	params: JobParams;
	run_id?: string;
};

export type ModelConfig = {
	id: string;
	display_name: string;
	region: string;
	model_type: string;
	model_dir: string;
	model_var: string;
	unit_cvt: number | null;
	file_pattern: string;
	probabilistic: boolean;
	members: string | null;
	init_days: string;
	date_filter_year?: number | null;
	start_date: string;
	end_date: string;
	start_year_clim: number;
	end_year_clim: number;
};

export type JobResult = {
	name: string;
	type: 'figure' | 'output';
	url: string;
};

export type JobArtifact = {
	id: string;
	kind: string;
	filename: string;
	media_type: string;
	size_bytes: number;
	checksum: string;
	created_at: string;
	url: string;
};

export async function getModels(region?: string) {
	const qs = region ? `?region=${encodeURIComponent(region)}` : '';
	return request<ModelConfig[]>(`/jobs/models${qs}`);
}

export async function getJobs() {
	return request<Job[]>('/jobs');
}

export async function submitJob(params: SubmitJobParams) {
	return request<Job>('/jobs', {
		method: 'POST',
		body: JSON.stringify(params)
	});
}

export async function getJob(id: string) {
	return request<Job>(`/jobs/${id}`);
}

export async function getJobResults(id: string) {
	return request<JobResult[]>(`/jobs/${id}/results`);
}

export async function getJobLogs(id: string) {
	return request<{ logs: string }>(`/jobs/${id}/logs`);
}

export async function deleteJob(id: string): Promise<void> {
	await request<void>(`/jobs/${id}`, { method: 'DELETE' });
}

export async function cancelJob(id: string): Promise<Job> {
	return request<Job>(`/jobs/${id}/cancel`, { method: 'POST' });
}

export async function shareJob(id: string): Promise<Job> {
	return request<Job>(`/jobs/${id}/share`, { method: 'POST' });
}

export async function unshareJob(id: string): Promise<Job> {
	return request<Job>(`/jobs/${id}/unshare`, { method: 'POST' });
}

export async function getJobArtifacts(id: string): Promise<JobArtifact[]> {
	return request<JobArtifact[]>(`/jobs/${id}/artifacts`);
}

// The blend's pooled summary CSV, read server-side so the browser never fetches
// the outputs bucket directly. Empty string until publication indexes it.
export async function getBlendSummary(id: string): Promise<string> {
	const { csv } = await request<{ csv: string }>(`/jobs/${id}/blend-summary`);
	return csv;
}

/**
 * Fetch a result file (figure/output) as an object URL for display.
 * Cached in memory by URL so repeated views don't re-fetch. The cache is
 * LRU-bounded: evicted entries have their object URLs revoked so the blobs
 * can be garbage collected during long sessions.
 */
const blobCache = new Map<string, string>();
const BLOB_CACHE_MAX_ENTRIES = 100;

function rememberBlob(resultUrl: string, objectUrl: string): void {
	blobCache.set(resultUrl, objectUrl);
	while (blobCache.size > BLOB_CACHE_MAX_ENTRIES) {
		const oldest = blobCache.keys().next().value!;
		URL.revokeObjectURL(blobCache.get(oldest)!);
		blobCache.delete(oldest);
	}
}

export async function fetchResultBlob(resultUrl: string): Promise<string> {
	const hit = blobCache.get(resultUrl);
	if (hit) {
		// Re-insert to mark as most recently used.
		blobCache.delete(resultUrl);
		blobCache.set(resultUrl, hit);
		return hit;
	}

	const res = await fetch(`${BASE_URL}${resultUrl}`, {
		headers: authHeaders(),
		redirect: 'manual'
	});

	let blob: Blob;
	if (res.type === 'opaqueredirect') {
		const location = res.headers.get('Location');
		if (!location) throw new Error('Redirect response missing Location header');
		const gcsRes = await fetch(location);
		if (!gcsRes.ok) throw new Error(`Failed to fetch result: ${gcsRes.status}`);
		blob = await gcsRes.blob();
	} else if (res.ok) {
		// Local dev: backend served the file directly.
		blob = await res.blob();
	} else {
		throw new Error(`Failed to fetch result: ${res.status}`);
	}

	const objectUrl = URL.createObjectURL(blob);
	rememberBlob(resultUrl, objectUrl);
	return objectUrl;
}

// ---- Job metrics / grid / cell -------------------------------------------------

export type MetricStats = {
	mean: number;
	min: number;
	max: number;
	p25: number;
	p50: number;
	p75: number;
	p90: number;
	unit: string;
};

export type WindowMetrics = {
	window: string;
	model: string;
	tolerance_days: number | null;
	metrics: Record<string, MetricStats>;
};

export type BboxExtent = {
	lat_min: number;
	lat_max: number;
	lon_min: number;
	lon_max: number;
};

export type GridInfo = {
	lats: number[];
	lons: number[];
};

export type JobMetrics = {
	job_id: string;
	windows: WindowMetrics[];
	grid: GridInfo | null;
	bbox: BboxExtent | null;
};

export type BboxFilter = Partial<BboxExtent>;

export async function getJobMetrics(id: string, bbox?: BboxFilter): Promise<JobMetrics> {
	const params = new URLSearchParams();
	if (bbox?.lat_min != null) params.set('lat_min', String(bbox.lat_min));
	if (bbox?.lat_max != null) params.set('lat_max', String(bbox.lat_max));
	if (bbox?.lon_min != null) params.set('lon_min', String(bbox.lon_min));
	if (bbox?.lon_max != null) params.set('lon_max', String(bbox.lon_max));
	const qs = params.size ? `?${params}` : '';
	return request<JobMetrics>(`/jobs/${id}/metrics${qs}`);
}

export type JobGridResponse = {
	job_id: string;
	model: string;
	window: string;
	metric: string;
	lats: number[];
	lons: number[];
	values: (number | null)[][];
	unit: string;
	min: number;
	max: number;
};

export type CellMetricComparison = {
	model: number | null;
	baseline: number | null;
	delta: number | null;
	unit: string;
};

export type CellMaePoint = {
	year: number;
	model: number | null;
	baseline: number | null;
	delta: number | null;
};

export type JobCellResponse = {
	job_id: string;
	model: string;
	window: string;
	requested_lat: number;
	requested_lon: number;
	lat: number;
	lon: number;
	metrics: Record<string, CellMetricComparison>;
	mae_series: CellMaePoint[];
};

export async function getJobGrid(
	id: string,
	model: string,
	window: string,
	metric: string
): Promise<JobGridResponse> {
	const params = new URLSearchParams({ model, window, metric });
	return request<JobGridResponse>(`/jobs/${id}/grid?${params}`);
}

export async function getJobCell(
	id: string,
	model: string,
	window: string,
	lat: number,
	lon: number
): Promise<JobCellResponse> {
	const params = new URLSearchParams({ model, window, lat: String(lat), lon: String(lon) });
	return request<JobCellResponse>(`/jobs/${id}/cell?${params}`);
}
