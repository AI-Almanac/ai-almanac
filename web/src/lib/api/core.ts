// API base URL — read at runtime from `window.__ALMANAC_CONFIG__` (injected by
// the backend's `/config.js`) so a single built SPA can target any backend.
// Falls back to the build-time Vite env (dev), then to same-origin.
import { authHeaders, login, refreshAuthTokens } from '../auth';
import { addBreadcrumb } from '../breadcrumbs';

declare global {
	interface Window {
		__ALMANAC_CONFIG__?: {
			apiUrl?: string;
			authMode?: 'none' | 'proxy' | 'globus';
			submittedByEnabled?: boolean;
			version?: string;
			feedbackEnabled?: boolean;
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
export const BASE_URL =
	typeof runtimeConfig?.apiUrl === 'string'
		? runtimeConfig.apiUrl
		: import.meta.env.VITE_API_URL || '';

export function isBackendUrl(
	url: string,
	baseUrl = BASE_URL,
	locationHref = typeof window !== 'undefined' ? window.location.href : 'http://localhost/'
): boolean {
	try {
		const requestUrl = new URL(url, locationHref);
		if (!baseUrl) return requestUrl.origin === new URL(locationHref).origin;
		const apiUrl = new URL(baseUrl, locationHref);
		return url.startsWith(baseUrl) || requestUrl.origin === apiUrl.origin;
	} catch {
		return false;
	}
}

export function usesBearerAuth(
	config: Window['__ALMANAC_CONFIG__'],
	globusClientId: string | undefined
): boolean {
	if (config) return config.authMode === 'globus';
	return Boolean(globusClientId);
}

export const USE_BEARER_AUTH = usesBearerAuth(runtimeConfig, import.meta.env.VITE_GLOBUS_CLIENT_ID);

export async function request<T>(
	path: string,
	init: RequestInit = {},
	retry = false,
	requireAuth = true
): Promise<T> {
	// No stored token means the user never signed in (or signed out): fail the
	// call and let the layout show the sign-in prompt. Redirecting to Globus
	// here bounced first-time visitors off-site before they saw the app (#182).
	const headers = authHeaders();
	if (requireAuth && USE_BEARER_AUTH && !('Authorization' in headers)) {
		throw new Error('Authentication required');
	}

	const method = init.method ?? 'GET';
	const start = performance.now();
	let res: Response;
	try {
		res = await fetch(`${BASE_URL}${path}`, {
			...init,
			headers: { 'Content-Type': 'application/json', ...headers, ...init.headers }
		});
	} catch (e) {
		addBreadcrumb('api', `${method} ${path} network error`, {
			method,
			path,
			error: e instanceof Error ? e.message : String(e)
		});
		throw e;
	}

	const durationMs = Math.round(performance.now() - start);
	addBreadcrumb('api', `${method} ${path} ${res.status} (${durationMs}ms)`, {
		method,
		path,
		status: res.status,
		durationMs,
		requestId: res.headers.get('x-request-id') ?? undefined
	});

	if (res.status === 401) {
		if (!retry && (await refreshAuthTokens())) {
			return request<T>(path, init, true);
		}
		// Refresh failed (or already retried once): the session is truly
		// expired. Send the user to re-authenticate instead of letting every
		// call site's Promise.allSettled swallow this as an empty result.
		if (requireAuth && USE_BEARER_AUTH) {
			login(window.location.pathname + window.location.search);
		}
	}

	if (!res.ok) {
		const body = await res.text();
		addBreadcrumb('api', `${method} ${path} failed (${res.status})`, {
			method,
			path,
			status: res.status,
			body,
			requestId: res.headers.get('x-request-id') ?? undefined
		});
		throw new Error(`${method} ${path} failed (${res.status}): ${body}`);
	}
	if (res.status === 204 || res.headers.get('content-length') === '0') {
		return undefined as T;
	}
	return res.json();
}
