// ---- LLM providers & profiles ------------------------------------------------
import { request } from './core';

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
