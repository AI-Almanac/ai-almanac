// API base URL — read at runtime from `window.__ALMANAC_CONFIG__` (injected by
// the backend's `/config.js`) so a single built SPA can target any backend.
// Falls back to the build-time Vite env (dev), then to same-origin.
import { authHeaders, getApiAccessToken, login, refreshAuthTokens } from './auth';

declare global {
	interface Window {
		__ALMANAC_CONFIG__?: {
			apiUrl?: string;
			authMode?: 'none' | 'proxy' | 'globus';
			submittedByEnabled?: boolean;
		};
	}
}

// The backend's `/config.js` is authoritative for deployments: when it is
// present, its `apiUrl` wins even when empty ("" means same-origin). Only fall
// back to the build-time Vite env (dev, where `/config.js` 404s) otherwise. A
// truthy `||` chain here would discard a same-origin "" in favour of a leaked
// build-time VITE_API_URL — which is exactly how a stray web/.env once pointed
// production at localhost.
const runtimeConfig = typeof window !== 'undefined' ? window.__ALMANAC_CONFIG__ : undefined;
const BASE_URL =
	typeof runtimeConfig?.apiUrl === 'string'
		? runtimeConfig.apiUrl
		: import.meta.env.VITE_API_URL || '';

export function usesBearerAuth(
	config: Window['__ALMANAC_CONFIG__'],
	globusClientId: string | undefined
): boolean {
	if (config) return config.authMode === 'globus';
	return Boolean(globusClientId);
}

const USE_BEARER_AUTH = usesBearerAuth(runtimeConfig, import.meta.env.VITE_GLOBUS_CLIENT_ID);

// ---- WebSocket job streaming ------------------------------------------------

export type JobStreamEvent =
	| { type: 'status'; payload: { status: string } }
	| { type: 'log'; payload: { line: string } }
	| { type: 'done'; payload: { status: string; exit_code?: number } }
	| { type: 'metric'; payload: Record<string, unknown> };

/**
 * Subscribe to live job events (status, log lines, completion). Replaces HTTP
 * polling of `/jobs/{id}` and `/jobs/{id}/logs`. Returns a closer that
 * unsubscribes when called.
 */
export function subscribeJob(
	jobId: string,
	onEvent: (event: JobStreamEvent) => void,
	onClose?: (clean: boolean) => void
): () => void {
	const wsBase = BASE_URL
		? BASE_URL.replace(/^https?:/, BASE_URL.startsWith('https:') ? 'wss:' : 'ws:')
		: `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
	const url = new URL(`/jobs/${jobId}/stream`, wsBase);
	const accessToken = getApiAccessToken();
	if (accessToken) url.searchParams.set('access_token', accessToken);
	const ws = new WebSocket(url);
	ws.onmessage = (e) => {
		try {
			onEvent(JSON.parse(e.data) as JobStreamEvent);
		} catch {
			/* ignore non-JSON frames */
		}
	};
	ws.onclose = (e) => onClose?.(e.wasClean);
	return () => ws.close();
}

async function request<T>(
	path: string,
	init: RequestInit = {},
	retry = false,
	requireAuth = true
): Promise<T> {
	const headers = authHeaders();
	if (requireAuth && USE_BEARER_AUTH && !('Authorization' in headers)) {
		login(window.location.pathname + window.location.search);
		throw new Error('Authentication required');
	}

	const res = await fetch(`${BASE_URL}${path}`, {
		...init,
		headers: { 'Content-Type': 'application/json', ...headers, ...init.headers }
	});

	if (res.status === 401 && !retry && (await refreshAuthTokens())) {
		return request<T>(path, init, true);
	}

	if (!res.ok) {
		const body = await res.text();
		throw new Error(`${init.method ?? 'GET'} ${path} failed (${res.status}): ${body}`);
	}
	if (res.status === 204 || res.headers.get('content-length') === '0') {
		return undefined as T;
	}
	return res.json();
}

// ---- Config ------------------------------------------------------------------

// ---- Filesystem browser -----------------------------------------------------

export interface FsEntry {
	name: string;
	kind: 'file' | 'dir';
	size: number | null;
	is_hidden: boolean;
}

export interface FsListing {
	path: string;
	parent: string | null;
	entries: FsEntry[];
}

export interface QuickPath {
	label: string;
	path: string;
}

export async function fsList(path = '', includeHidden = false): Promise<FsListing> {
	const params = new URLSearchParams();
	if (path) params.set('path', path);
	if (includeHidden) params.set('include_hidden', 'true');
	const q = params.toString();
	return request<FsListing>(`/fs/list${q ? '?' + q : ''}`);
}

export async function fsQuickPaths(): Promise<QuickPath[]> {
	return request<QuickPath[]>('/fs/quick-paths');
}

// ---- Settings ---------------------------------------------------------------

export interface SettingsField {
	name: string;
	label: string;
	description: string;
	type: 'string' | 'int' | 'float' | 'bool';
	default: unknown;
	sensitive: boolean;
	restart_required: boolean;
	editable: boolean;
}

export interface SettingsGroup {
	name: string;
	fields: SettingsField[];
}

export interface SettingsSchema {
	deployment_mode: string;
	groups: SettingsGroup[];
}

export async function getSettingsSchema(): Promise<SettingsSchema> {
	return request<SettingsSchema>('/settings/schema');
}

export async function getSettings(reveal = false): Promise<Record<string, unknown>> {
	const q = reveal ? '?reveal=true' : '';
	const res = await request<{ values: Record<string, unknown> }>(`/settings${q}`);
	return res.values;
}

export async function patchSettings(
	values: Record<string, unknown>
): Promise<Record<string, unknown>> {
	const res = await request<{ values: Record<string, unknown> }>('/settings', {
		method: 'PATCH',
		body: JSON.stringify({ values })
	});
	return res.values;
}

export async function getConfigYamlPath(): Promise<string> {
	const res = await request<{ path: string }>('/settings/config-yaml-path');
	return res.path;
}

// ---- Data sources -----------------------------------------------------------

export interface DataSource {
	id: string;
	kind: 'obs' | 'model';
	name: string;
	path: string;
	region: string | null;
	metadata: Record<string, unknown>;
	location_type: 'local_directory';
	status: 'ready' | 'invalid';
	validation_error: string | null;
	created_at: string;
	updated_at: string | null;
}

export interface DataSourceCreate {
	kind: 'obs' | 'model';
	name: string;
	path: string;
	region?: string;
	metadata?: Record<string, unknown>;
}

export interface DataSourceValidation {
	kind: 'obs' | 'model';
	path: string;
	region: string;
	metadata: Record<string, unknown>;
	status: 'ready' | 'invalid';
	validation_error: string | null;
}

export async function listDataSources(kind?: 'obs' | 'model'): Promise<DataSource[]> {
	const q = kind ? `?kind=${kind}` : '';
	return request<DataSource[]>(`/data-sources${q}`);
}

export async function validateDataSource(body: DataSourceCreate): Promise<DataSourceValidation> {
	return request<DataSourceValidation>('/data-sources/validate', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export async function createDataSource(body: DataSourceCreate): Promise<DataSource> {
	return request<DataSource>('/data-sources', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export async function updateDataSource(
	id: string,
	body: Omit<DataSourceCreate, 'kind'>
): Promise<DataSource> {
	return request<DataSource>(`/data-sources/${id}`, {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

export async function revalidateDataSource(id: string): Promise<DataSource> {
	return request<DataSource>(`/data-sources/${id}/revalidate`, { method: 'POST' });
}

export async function deleteDataSource(id: string): Promise<void> {
	await request<void>(`/data-sources/${id}`, { method: 'DELETE' });
}

export async function getMetricDefinitions() {
	return request<MetricDefinition[]>('/config/metrics');
}

export async function getRompDefaults() {
	return request<RompDefaults>('/config/romp-defaults');
}

export type AppCapabilities = {
	chat: boolean;
};

export async function getCapabilities(): Promise<AppCapabilities> {
	return request<AppCapabilities>('/config/capabilities');
}

// ---- LLM providers & profiles ------------------------------------------------

export type LlmProviderType = 'openai-compatible' | 'pydantic-ai';

export type LlmProvider = {
	id: string;
	provider_type: LlmProviderType;
	display_name: string;
	base_url: string | null;
	enabled: boolean;
	allow_shared: boolean;
	shared_model_name: string | null;
	has_shared_key: boolean;
};

export type LlmProfile = {
	id: string;
	provider_id: string;
	provider_display_name: string;
	model_name: string;
	is_default: boolean;
	has_api_key: boolean;
	created_at: string;
	updated_at: string;
};

export type LlmStatus = {
	preference: 'auto' | 'shared' | 'own';
	shared_available: boolean;
	has_own_default: boolean;
	effective_source: 'own' | 'shared' | null;
};

export function listLlmProviders() {
	return request<LlmProvider[]>('/llm/providers');
}

export function createLlmProvider(body: {
	provider_type: LlmProviderType;
	display_name: string;
	base_url?: string | null;
	enabled?: boolean;
}) {
	return request<LlmProvider>('/llm/providers', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export function setProviderShared(
	providerId: string,
	body: { allow_shared: boolean; shared_model_name?: string | null; api_key?: string }
) {
	return request<LlmProvider>(`/llm/providers/${providerId}/shared`, {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

export function listLlmProfiles() {
	return request<LlmProfile[]>('/llm/profiles');
}

export function createLlmProfile(body: {
	provider_id: string;
	model_name: string;
	api_key: string;
	is_default?: boolean;
}) {
	return request<LlmProfile>('/llm/profiles', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export function setDefaultLlmProfile(profileId: string) {
	return request<LlmProfile>(`/llm/profiles/${profileId}/default`, { method: 'POST' });
}

export function deleteLlmProfile(profileId: string) {
	return request<void>(`/llm/profiles/${profileId}`, { method: 'DELETE' });
}

export function testLlmProfile(profileId: string) {
	return request<{ status: string; latency_ms?: number }>(`/llm/profiles/${profileId}/test`, {
		method: 'POST'
	});
}

export function getLlmStatus() {
	return request<LlmStatus>('/llm/status');
}

export function setLlmPreference(preference: LlmStatus['preference']) {
	return request<LlmStatus>('/llm/preference', {
		method: 'PUT',
		body: JSON.stringify({ preference })
	});
}

// ---- Account / identity ------------------------------------------------------

export type Account = {
	id: string;
	subject: string;
	email: string | null;
	display_name: string | null;
	role: 'admin' | 'user';
	deployment_mode: 'personal' | 'shared';
	capabilities: {
		can_admin: boolean;
		can_browse_fs: boolean;
		can_run_code: boolean;
	};
};

export async function getAccount(): Promise<Account> {
	return request<Account>('/auth/me', {}, false, false);
}

// ---- Regions -----------------------------------------------------------------

export async function getRegions() {
	return request<Region[]>('/regions');
}

export type RegionWrite = {
	display_name: string;
	description: string;
	lat_min: number;
	lat_max: number;
	lon_min: number;
	lon_max: number;
	land_only: boolean;
};

export async function createRegion(body: RegionWrite) {
	return request<Region>('/regions', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export async function updateRegion(id: string, body: RegionWrite) {
	return request<Region>(`/regions/${encodeURIComponent(id)}`, {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

export async function deleteRegion(id: string): Promise<void> {
	await request<void>(`/regions/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function getRegionBoundary(region: string, level: BoundaryLevel) {
	return request<RegionBoundaryResponse>(
		`/regions/${encodeURIComponent(region)}/boundaries/${encodeURIComponent(level)}`
	);
}

// ---- Datasets ----------------------------------------------------------------

export async function getDatasets() {
	return request<Dataset[]>('/datasets');
}

export async function createDatasetFromPath(name: string, obs_dir: string) {
	return request<Dataset>('/datasets/from-path', {
		method: 'POST',
		body: JSON.stringify({ name, obs_dir })
	});
}

// ---- Jobs --------------------------------------------------------------------

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

export async function getJobArtifacts(id: string): Promise<JobArtifact[]> {
	return request<JobArtifact[]>(`/jobs/${id}/artifacts`);
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

// ---- Blends ------------------------------------------------------------------

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

// ---- Types -------------------------------------------------------------------

export type Region = {
	id: string;
	display_name: string;
	romp_region: string;
	description: string;
	has_data: boolean;
	source_count: number;
	lat_min: number | null;
	lat_max: number | null;
	lon_min: number | null;
	lon_max: number | null;
	land_only: boolean;
	shp_only: boolean;
	is_builtin: boolean;
	boundary_iso: string | null;
};

export type BoundaryLevel = 'adm1' | 'adm2';

export type RegionBoundaryMetadata = {
	boundaryID: string | null;
	boundaryName: string | null;
	boundaryType: string | null;
	boundarySource: string | null;
	boundaryLicense: string | null;
	licenseSource: string | null;
};

export type RegionBoundaryResponse = {
	metadata: RegionBoundaryMetadata;
	geojson: unknown;
};

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

export type BenchmarkRunSpec = {
	intent: string;
	region_id?: string | null;
	region_name?: string | null;
	romp_region?: string | null;
	event_type: string;
	dataset_id?: string | null;
	dataset_name?: string | null;
	model_ids: string[];
	model_names: string[];
	forecast_window_days?: number | null;
	status: 'collecting' | 'needs_confirmation' | 'runnable' | 'running';
	missing_fields: string[];
	assumptions: string[];
	advanced_params: Record<string, unknown>;
};

export type BenchmarkSubmitResponse = {
	run_id: string;
	jobs: Job[];
	benchmark_config: BenchmarkRunSpec;
	benchmark_validation: BenchmarkValidation;
};

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

// ---- Chat --------------------------------------------------------------------

export type ChatSession = {
	id: string;
	title: string | null;
	created_at: string;
	updated_at: string;
	message_count: number;
	scope: ChatScope;
	benchmark_config?: BenchmarkRunSpec | null;
	benchmark_validation?: BenchmarkValidation | null;
	run_id?: string | null;
};

export type ChatMessage = {
	id: string;
	role: 'user' | 'assistant';
	content: string;
	created_at: string;
	tool_calls?: ChatToolCall[];
	artifacts?: ChatArtifact[];
};

export type ChatScope = {
	kind: 'benchmark_setup' | 'benchmark_run_group' | 'job_set';
	key: string;
	title?: string | null;
	job_ids: string[];
};

export type BenchmarkValidation = {
	can_run: boolean;
	status: BenchmarkRunSpec['status'];
	missing_fields: string[];
	errors: string[];
	warnings: string[];
};

export type ChatArtifact = {
	id: string;
	kind: 'figure';
	url: string;
	label?: string | null;
	filename?: string | null;
	media_type?: string | null;
	created_at: string;
};

export type ChatToolCall = {
	id: string;
	name: string;
	status: 'running' | 'completed' | 'failed';
	input: Record<string, unknown>;
	result?: unknown;
	artifacts: ChatArtifact[];
};

export type ChatSessionDetail = ChatSession & {
	scope: ChatScope;
	transcript: ChatMessage[];
};

export type ChatEvent =
	| { type: 'text_delta'; turn_id: string; content: string }
	| { type: 'tool_call'; turn_id: string; tool_call: ChatToolCall }
	| {
			type: 'tool_result';
			turn_id: string;
			tool_call_id: string;
			status: ChatToolCall['status'];
			result: unknown;
	  }
	| { type: 'artifact'; turn_id: string; tool_call_id: string; artifact: ChatArtifact }
	| {
			type: 'tool_approval_request';
			turn_id: string;
			tool_call: ChatToolCall;
			metadata?: Record<string, unknown>;
	  }
	| {
			type: 'benchmark_config';
			turn_id: string;
			config: BenchmarkRunSpec;
			validation?: BenchmarkValidation | null;
			run_id?: string | null;
			jobs?: Job[] | null;
	  }
	| {
			type: 'benchmark_approval_request';
			turn_id: string;
			tool_call_id: string;
			config: BenchmarkRunSpec;
			validation?: BenchmarkValidation | null;
	  }
	| { type: 'error'; message: string; error_type?: string; retryable?: boolean }
	| { type: 'done'; turn: ChatMessage };

export async function createChatSession(scope: ChatScope, title?: string): Promise<ChatSession> {
	return request<ChatSession>('/chat/sessions', {
		method: 'POST',
		body: JSON.stringify({ scope, title })
	});
}

export async function getChatSessions(scope?: ChatScope): Promise<ChatSession[]> {
	const qs = scope
		? `?scope_kind=${encodeURIComponent(scope.kind)}&scope_key=${encodeURIComponent(scope.key)}`
		: '';
	return request<ChatSession[]>(`/chat/sessions${qs}`);
}

export async function getChatSession(id: string): Promise<ChatSessionDetail> {
	return request<ChatSessionDetail>(`/chat/sessions/${id}`);
}

export async function updateChatSession(
	id: string,
	updates: { title?: string | null }
): Promise<ChatSession> {
	return request<ChatSession>(`/chat/sessions/${id}`, {
		method: 'PATCH',
		body: JSON.stringify(updates)
	});
}

export async function deleteChatSession(id: string): Promise<void> {
	await request<void>(`/chat/sessions/${id}`, { method: 'DELETE' });
}

export async function submitChatBenchmark(
	sessionId: string,
	approval?: { tool_call_id: string; approved_config?: BenchmarkRunSpec | null }
): Promise<BenchmarkSubmitResponse> {
	return request<BenchmarkSubmitResponse>(`/chat/sessions/${sessionId}/benchmark/submit`, {
		method: 'POST',
		body: approval
			? JSON.stringify({
					approval: {
						tool_call_id: approval.tool_call_id,
						approved_config: approval.approved_config ?? null
					}
				})
			: undefined
	});
}

export async function denyChatBenchmarkApproval(
	sessionId: string,
	approval: { tool_call_id: string; approved_config?: BenchmarkRunSpec | null; message?: string }
): Promise<void> {
	return request<void>(`/chat/sessions/${sessionId}/benchmark/approval`, {
		method: 'POST',
		body: JSON.stringify({
			approval: {
				tool_call_id: approval.tool_call_id,
				approved_config: approval.approved_config ?? null
			},
			message: approval.message ?? 'The user declined to run the benchmark.'
		})
	});
}

export async function updateChatBenchmarkConfig(
	sessionId: string,
	patch: Partial<BenchmarkRunSpec>
): Promise<{ benchmark_config: BenchmarkRunSpec; benchmark_validation: BenchmarkValidation }> {
	return request<{ benchmark_config: BenchmarkRunSpec; benchmark_validation: BenchmarkValidation }>(
		`/chat/sessions/${sessionId}/benchmark/config`,
		{
			method: 'PATCH',
			body: JSON.stringify(patch)
		}
	);
}

/**
 * Send a message and return an async generator of ChatEvents parsed from SSE.
 */
export async function* sendChatMessage(
	sessionId: string,
	content: string,
	scope?: ChatScope
): AsyncGenerator<ChatEvent> {
	const res = await fetch(`${BASE_URL}/chat/sessions/${sessionId}/message`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', ...authHeaders() },
		body: JSON.stringify({ content, scope })
	});
	if (!res.ok) {
		const body = await res.text();
		throw new Error(`Chat message failed (${res.status}): ${body}`);
	}

	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	let buffer = '';
	let sawTerminalEvent = false;

	const parseLine = (line: string): ChatEvent | null => {
		if (!line.startsWith('data: ')) return null;
		try {
			return JSON.parse(line.slice(6)) as ChatEvent;
		} catch {
			return null;
		}
	};

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		const lines = buffer.split('\n');
		buffer = lines.pop()!;
		for (const line of lines) {
			const event = parseLine(line);
			if (!event) continue;
			yield event;
			if (event.type === 'done' || event.type === 'error') {
				sawTerminalEvent = true;
				return;
			}
		}
	}

	const finalEvent = parseLine(buffer.trimEnd());
	if (finalEvent) {
		yield finalEvent;
		if (finalEvent.type === 'done' || finalEvent.type === 'error') {
			sawTerminalEvent = true;
		}
	}

	if (!sawTerminalEvent) {
		throw new Error('Chat stream ended before a terminal event was received.');
	}
}
