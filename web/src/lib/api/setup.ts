/**
 * Setup API client — used only during the first-run wizard.
 *
 * Deliberately avoids core.ts's `request()` so it never triggers the normal
 * 401/auth flow before setup is complete. Every call attaches X-Setup-Token
 * from sessionStorage.
 */
import { BASE_URL } from './core';
import { sseEvents } from './sse';
import type { components } from '$lib/api-types.gen';

export type SetupState = components['schemas']['SetupState'];
export type LlmInput = components['schemas']['LlmInput'];
export type LlmTestOut = components['schemas']['LlmTestOut'];
export type StorageInput = components['schemas']['StorageInput'];
export type PrepareInput = components['schemas']['PrepareInput'];
export type PrepareStatus = components['schemas']['PrepareStatus'];
export type FinishOut = components['schemas']['FinishOut'];

const SESSION_KEY = 'almanac-setup-token';

export function getSetupToken(): string | null {
	if (typeof sessionStorage === 'undefined') return null;
	return sessionStorage.getItem(SESSION_KEY);
}

export function storeSetupToken(token: string): void {
	sessionStorage.setItem(SESSION_KEY, token);
}

function setupHeaders(): Record<string, string> {
	const token = getSetupToken();
	const h: Record<string, string> = { 'Content-Type': 'application/json' };
	if (token) h['X-Setup-Token'] = token;
	return h;
}

async function setupFetch<T>(
	path: string,
	init: RequestInit = {}
): Promise<{ ok: boolean; status: number; data: T | null; error: string | null }> {
	try {
		const res = await fetch(`${BASE_URL}${path}`, {
			...init,
			headers: { ...setupHeaders(), ...(init.headers as Record<string, string> | undefined) }
		});
		if (res.status === 204) return { ok: true, status: 204, data: null, error: null };
		const body = await res.text();
		if (!res.ok) {
			let detail = body;
			try {
				detail = JSON.parse(body).detail ?? body;
			} catch {
				// leave as-is
			}
			return { ok: false, status: res.status, data: null, error: String(detail) };
		}
		return { ok: true, status: res.status, data: JSON.parse(body) as T, error: null };
	} catch (e) {
		return { ok: false, status: 0, data: null, error: e instanceof Error ? e.message : String(e) };
	}
}

export async function getSetupState(): Promise<SetupState> {
	const r = await setupFetch<SetupState>('/api/setup/state');
	if (!r.ok || !r.data) throw new Error(r.error ?? 'Failed to load setup state');
	return r.data;
}

export async function saveStorage(input: StorageInput): Promise<void> {
	const r = await setupFetch<null>('/api/setup/storage', {
		method: 'POST',
		body: JSON.stringify(input)
	});
	if (!r.ok) throw new Error(r.error ?? 'Failed to save storage settings');
}

export async function testLlm(input: LlmInput): Promise<LlmTestOut> {
	const r = await setupFetch<LlmTestOut>('/api/setup/llm', {
		method: 'POST',
		body: JSON.stringify({ ...input, test_only: true })
	});
	if (!r.ok || !r.data) throw new Error(r.error ?? 'LLM test failed');
	return r.data;
}

export async function saveLlm(input: LlmInput): Promise<LlmTestOut> {
	const r = await setupFetch<LlmTestOut>('/api/setup/llm', {
		method: 'POST',
		body: JSON.stringify({ ...input, test_only: false })
	});
	if (!r.ok || !r.data) throw new Error(r.error ?? 'Failed to save LLM settings');
	return r.data;
}

export async function startPrepare(input: PrepareInput): Promise<PrepareStatus> {
	const r = await setupFetch<PrepareStatus>('/api/setup/envs/prepare', {
		method: 'POST',
		body: JSON.stringify(input)
	});
	if (!r.ok || !r.data) throw new Error(r.error ?? 'Failed to start env preparation');
	return r.data;
}

export type PrepareEvent =
	| { type: 'state'; seq: number; status: string; envs: Record<string, string> }
	| { type: 'env'; seq: number; kind: string; phase: string; line?: string; detail?: string }
	| { type: 'done'; seq: number; ok: boolean; error: string | null; envs: Record<string, string> }
	| { type: 'keepalive' };

export async function* streamPrepareEvents(after: number): AsyncGenerator<PrepareEvent> {
	const token = getSetupToken();
	const headers: Record<string, string> = { Accept: 'text/event-stream' };
	if (token) headers['X-Setup-Token'] = token;

	const res = await fetch(`${BASE_URL}/api/setup/envs/events?after=${after}`, { headers });
	if (!res.ok) throw new Error(`SSE stream failed (${res.status})`);
	yield* sseEvents<PrepareEvent>(res);
}

export async function finishSetup(): Promise<FinishOut> {
	const r = await setupFetch<FinishOut>('/api/setup/finish', { method: 'POST' });
	if (!r.ok || !r.data) throw new Error(r.error ?? 'Failed to finish setup');
	return r.data;
}
