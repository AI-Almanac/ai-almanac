import { browser } from '$app/environment';
import { authorization } from '@globus/sdk';
import type { AuthorizationManager } from '@globus/sdk/core/authorization/AuthorizationManager';
import type { StoredToken } from '@globus/sdk/core/authorization/TokenManager';

const API_RESOURCE_SERVER = '50964632-afc7-4d4c-abf4-b288cc18a3af';
const API_SCOPE = `https://auth.globus.org/scopes/${API_RESOURCE_SERVER}/api`;

let manager: AuthorizationManager | null = null;

export function getManager(): AuthorizationManager | null {
	if (!browser) return null;

	const client = import.meta.env.VITE_GLOBUS_CLIENT_ID;
	const redirect = import.meta.env.VITE_GLOBUS_REDIRECT_URL || `${window.location.origin}/callback`;

	if (!client || !redirect) {
		console.warn('auth: VITE_GLOBUS_CLIENT_ID or VITE_GLOBUS_REDIRECT_URL is not set.');
		return null;
	}

	if (!manager) {
		manager = authorization.create({
			client,
			redirect,
			storage: localStorage,
			scopes: `${API_SCOPE} offline_access`,
			useRefreshTokens: true
		});
	}
	return manager;
}

export function getApiAccessToken(): string | null {
	const authManager = getManager();
	const token =
		authManager?.tokens.getByResourceServer(API_RESOURCE_SERVER) ??
		authManager?.getGlobusAuthToken()?.other_tokens?.find((candidate: StoredToken) => {
			return candidate.resource_server === API_RESOURCE_SERVER;
		});
	return token?.access_token ?? null;
}

export function hasApiAccessToken(): boolean {
	return Boolean(getApiAccessToken());
}

export function authHeaders(): HeadersInit {
	const accessToken = getApiAccessToken();
	return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

export async function refreshAuthTokens(): Promise<boolean> {
	const authManager = getManager();
	if (!authManager) return false;
	try {
		await authManager.refreshTokens();
		return true;
	} catch {
		await authManager.revoke();
		return false;
	}
}

let loginStarted = false;

export function login(returnTo?: string): void {
	if (!browser || loginStarted) return;
	loginStarted = true;
	sessionStorage.setItem(
		'auth_return_to',
		returnTo ?? window.location.pathname + window.location.search
	);
	void getManager()?.login();
}
