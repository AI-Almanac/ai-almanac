import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({ getAccount: vi.fn() }));
const signIn = vi.hoisted(() => ({ bearerAuth: true, hasToken: false }));

vi.mock('../src/lib/api', () => apiMocks);
vi.mock('../src/lib/api/core', () => ({
	get USE_BEARER_AUTH() {
		return signIn.bearerAuth;
	}
}));
vi.mock('../src/lib/auth', () => ({ hasApiAccessToken: () => signIn.hasToken }));

import { account } from '../src/lib/account.svelte';

function accountResponse(anonymous: boolean) {
	return {
		anonymous,
		id: anonymous ? null : 'u1',
		subject: anonymous ? null : 'user@example.org',
		email: anonymous ? null : 'user@example.org',
		display_name: null,
		role: 'user',
		deployment_mode: 'shared',
		capabilities: {
			can_admin: false,
			can_browse_fs: false,
			can_manage_data: false,
			can_use_forecasting: true,
			can_run_code: false
		}
	};
}

describe('whether the visitor is signed in', () => {
	beforeEach(() => {
		account.account = null;
		account.loaded = false;
		apiMocks.getAccount.mockReset();
		signIn.bearerAuth = true;
		signIn.hasToken = false;
	});

	it('treats a visitor the server calls anonymous as signed out', async () => {
		apiMocks.getAccount.mockResolvedValue(accountResponse(true));

		await account.reload();

		expect(account.isAnonymous).toBe(true);
	});

	it('treats a signed-in account as signed in', async () => {
		apiMocks.getAccount.mockResolvedValue(accountResponse(false));

		await account.reload();

		expect(account.isAnonymous).toBe(false);
	});

	it('asks a visitor with no sign-in to sign in when the account cannot be loaded', async () => {
		// Treating this as signed in showed setup forms whose requests all failed.
		apiMocks.getAccount.mockRejectedValue(new Error('GET /auth/me failed (502)'));

		await account.reload();

		expect(account.isAnonymous).toBe(true);
	});

	it('keeps a signed-in user signed in when the account cannot be loaded', async () => {
		signIn.hasToken = true;
		apiMocks.getAccount.mockRejectedValue(new Error('GET /auth/me failed (502)'));

		await account.reload();

		expect(account.isAnonymous).toBe(false);
	});

	it('never asks for sign-in on an install without sign-in', async () => {
		signIn.bearerAuth = false;
		apiMocks.getAccount.mockRejectedValue(new Error('GET /auth/me failed (502)'));

		await account.reload();

		expect(account.isAnonymous).toBe(false);
	});

	it('does not ask for sign-in while the account is still loading', () => {
		apiMocks.getAccount.mockReturnValue(new Promise(() => {}));

		void account.reload();

		expect(account.isAnonymous).toBe(false);
	});
});
