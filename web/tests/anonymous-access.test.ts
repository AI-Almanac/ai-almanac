import { beforeEach, describe, expect, it, vi } from 'vitest';

const authMocks = vi.hoisted(() => ({
	authHeaders: vi.fn<() => Record<string, string>>(() => ({})),
	refreshAuthTokens: vi.fn(async () => false),
	login: vi.fn()
}));

vi.mock('../src/lib/auth', () => authMocks);

import { isAnonRoute } from '../src/lib/anon-routes';
import { request } from '../src/lib/api/core';

describe('isAnonRoute', () => {
	it('allows the public read-only pages', () => {
		for (const path of ['/', '/almanac', '/benchmarks', '/blends', '/forecasts', '/privacy']) {
			expect(isAnonRoute(path)).toBe(true);
		}
		expect(isAnonRoute('/benchmarks/')).toBe(true);
	});

	it('keeps everything else behind the sign-in prompt', () => {
		for (const path of ['/user', '/settings', '/data-sources', '/benchmarks/nested']) {
			expect(isAnonRoute(path)).toBe(false);
		}
	});
});

describe('request without a token', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
		authMocks.authHeaders.mockReturnValue({});
		authMocks.refreshAuthTokens.mockClear();
		authMocks.login.mockClear();
	});

	it('sends the request anonymously instead of throwing', async () => {
		const fetchMock = vi
			.spyOn(globalThis, 'fetch')
			.mockResolvedValue(new Response(JSON.stringify([{ id: 'example' }]), { status: 200 }));

		await expect(request('/jobs')).resolves.toEqual([{ id: 'example' }]);
		const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>;
		expect('Authorization' in headers).toBe(false);
	});

	it('throws on 401 without attempting a token refresh or redirect', async () => {
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('nope', { status: 401 }));

		await expect(request('/jobs', { method: 'POST' })).rejects.toThrow('401');
		expect(authMocks.refreshAuthTokens).not.toHaveBeenCalled();
		expect(authMocks.login).not.toHaveBeenCalled();
	});
});

describe('request with a token', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
		authMocks.authHeaders.mockReturnValue({ Authorization: 'Bearer tok' });
		authMocks.refreshAuthTokens.mockClear();
		authMocks.login.mockClear();
	});

	it('still refreshes and retries once on 401', async () => {
		authMocks.refreshAuthTokens.mockResolvedValue(true);
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response('expired', { status: 401 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));

		await expect(request('/jobs')).resolves.toEqual({ ok: true });
		expect(authMocks.refreshAuthTokens).toHaveBeenCalledTimes(1);
	});

	it('throws when the refresh fails', async () => {
		authMocks.refreshAuthTokens.mockResolvedValue(false);
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('expired', { status: 401 }));

		await expect(request('/jobs')).rejects.toThrow('401');
		expect(authMocks.refreshAuthTokens).toHaveBeenCalledTimes(1);
	});
});
