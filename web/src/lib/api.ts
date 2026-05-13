const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

import { getManager } from './auth';

function authHeaders(): HeadersInit {
	const manager = getManager();
	if (!manager) throw new Error('Not authenticated');
	const authToken = manager.getGlobusAuthToken();
	const apiToken = authToken?.other_tokens?.find(
		(t: any) => t.resource_server === '50964632-afc7-4d4c-abf4-b288cc18a3af'
	);
	if (!apiToken) throw new Error('API token not found — re-login may be required');
	return { Authorization: `Bearer ${apiToken.access_token}` };
}

async function request<T>(path: string, init: RequestInit = {}, _retry = false): Promise<T> {
	const res = await fetch(`${BASE_URL}${path}`, {
		...init,
		headers: { 'Content-Type': 'application/json', ...authHeaders(), ...init.headers }
	});

	if (res.status === 401 && !_retry) {
		const manager = getManager();
		if (manager) {
			try {
				await manager.refreshTokens();
				return request<T>(path, init, true);
			} catch {
				await manager.revoke();
				throw new Error('Session expired — please log in again.');
			}
		}
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

export async function getMetricDefinitions() {
	return request<MetricDefinition[]>('/config/metrics');
}

export async function getRompDefaults() {
	return request<RompDefaults>('/config/romp-defaults');
}

// ---- Regions -----------------------------------------------------------------

export async function getRegions() {
	return request<Region[]>('/regions');
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

/**
 * Fetch a result file (figure/output) as an object URL for display.
 * The backend requires auth headers so we can't use a plain <img src=...>.
 * Results are cached in memory by URL so repeated views don't re-fetch.
 */
const blobCache = new Map<string, string>();

export async function fetchResultBlob(resultUrl: string): Promise<string> {
	if (blobCache.has(resultUrl)) return blobCache.get(resultUrl)!;

	// First, hit the backend with auth to get the file or a signed-URL redirect.
	const res = await fetch(`${BASE_URL}${resultUrl}`, {
		headers: authHeaders(),
		redirect: 'manual' // don't auto-follow so we can strip auth before GCS redirect
	});

	let blob: Blob;
	if (res.type === 'opaqueredirect') {
		// Production: backend returned a 302 to a GCS signed URL.
		// Fetch the redirect location without the Authorization header — GCS rejects it.
		const location = res.headers.get('Location');
		if (!location) throw new Error('Redirect response missing Location header');
		const gcsRes = await fetch(location);
		if (!gcsRes.ok) throw new Error(`Failed to fetch result from GCS: ${gcsRes.status}`);
		blob = await gcsRes.blob();
	} else if (res.ok) {
		// Local dev: backend served the file directly.
		blob = await res.blob();
	} else {
		throw new Error(`Failed to fetch result: ${res.status}`);
	}

	const objectUrl = URL.createObjectURL(blob);
	blobCache.set(resultUrl, objectUrl);
	return objectUrl;
}

// ---- Types -------------------------------------------------------------------

export type Region = {
	id: string;
	display_name: string;
	romp_region: string;
	description: string;
	has_data: boolean;
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

export type JobStatus = 'running' | 'complete' | 'failed';

export type Job = {
	id: string;
	status: JobStatus;
	dataset_id: string;
	model_name: string;
	model_dir?: string;
	obs_dir?: string;
	params?: JobParams;
	created_at?: string;
	started_at?: string;
	completed_at?: string;
	error?: string | null;
	is_owner?: boolean;
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
