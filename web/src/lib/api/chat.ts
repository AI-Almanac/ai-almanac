// ---- Chat: sessions, streaming, and chat-driven benchmark/blend runs ----------
import { authHeaders } from '../auth';
import { BASE_URL, request } from './core';
import type { Job } from './jobs';
import type { Blend } from './blends';

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

export type BenchmarkValidation = {
	can_run: boolean;
	status: BenchmarkRunSpec['status'];
	missing_fields: string[];
	errors: string[];
	warnings: string[];
};

export type BenchmarkSubmitResponse = {
	run_id: string;
	jobs: Job[];
	benchmark_config: BenchmarkRunSpec;
	benchmark_validation: BenchmarkValidation;
};

export type BlendRunSpec = {
	intent: string;
	name: string;
	obs_dataset_id?: string | null;
	obs_dataset_name?: string | null;
	region_id?: string | null;
	model_ids: string[];
	model_names: string[];
	training_years: string;
	cv_holdout_years: string;
	forecast_years: string;
	true_holdout_years: string;
	formula_text: string;
	status: 'collecting' | 'runnable' | 'running';
	missing_fields: string[];
	assumptions: string[];
};

export type BlendValidation = {
	can_run: boolean;
	status: BlendRunSpec['status'];
	missing_fields: string[];
	errors: string[];
	warnings: string[];
};

export type BlendSubmitResponse = {
	run_id: string;
	jobs: Blend[];
	blend_config: BlendRunSpec;
	blend_validation: BlendValidation;
};

export type ChatScope = {
	kind: 'benchmark_setup' | 'benchmark_run_group' | 'blend_setup' | 'job_set';
	key: string;
	title?: string | null;
	job_ids: string[];
};

export type ChatSession = {
	id: string;
	title: string | null;
	created_at: string;
	updated_at: string;
	message_count: number;
	scope: ChatScope;
	benchmark_config?: BenchmarkRunSpec | null;
	benchmark_validation?: BenchmarkValidation | null;
	blend_config?: BlendRunSpec | null;
	blend_validation?: BlendValidation | null;
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
	| {
			type: 'blend_config';
			turn_id: string;
			config: BlendRunSpec;
			validation?: BlendValidation | null;
			run_id?: string | null;
			jobs?: Blend[] | null;
	  }
	| {
			type: 'blend_approval_request';
			turn_id: string;
			tool_call_id: string;
			config: BlendRunSpec;
			validation?: BlendValidation | null;
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

export async function submitChatBlend(
	sessionId: string,
	approval?: { tool_call_id: string; approved_config?: BlendRunSpec | null }
): Promise<BlendSubmitResponse> {
	return request<BlendSubmitResponse>(`/chat/sessions/${sessionId}/blend/submit`, {
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

export async function denyChatBlendApproval(
	sessionId: string,
	approval: { tool_call_id: string; approved_config?: BlendRunSpec | null; message?: string }
): Promise<void> {
	return request<void>(`/chat/sessions/${sessionId}/blend/approval`, {
		method: 'POST',
		body: JSON.stringify({
			approval: {
				tool_call_id: approval.tool_call_id,
				approved_config: approval.approved_config ?? null
			},
			message: approval.message ?? 'The user declined to train the blend.'
		})
	});
}

export async function updateChatBlendConfig(
	sessionId: string,
	patch: Partial<BlendRunSpec>
): Promise<{ blend_config: BlendRunSpec; blend_validation: BlendValidation }> {
	return request<{ blend_config: BlendRunSpec; blend_validation: BlendValidation }>(
		`/chat/sessions/${sessionId}/blend/config`,
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
