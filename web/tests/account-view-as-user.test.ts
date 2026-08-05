import { beforeEach, describe, expect, it } from 'vitest';

import { account } from '$lib/account.svelte';
import type { Account } from '$lib/api';

function asAdmin(canAdmin: boolean): Account {
	return {
		id: 'u1',
		subject: 'admin@example.org',
		issuer: 'proxy',
		email: 'admin@example.org',
		display_name: 'Admin',
		groups: [],
		role: canAdmin ? 'admin' : 'user',
		deployment_mode: 'shared',
		capabilities: {
			can_admin: canAdmin,
			can_browse_fs: false,
			can_manage_data: true,
			can_use_forecasting: true,
			can_run_code: false
		}
	} as unknown as Account;
}

describe('viewing as a regular user', () => {
	beforeEach(() => {
		account.setViewingAsUser(false);
		account.account = asAdmin(true);
	});

	it('hides admin UI without forgetting the real role', () => {
		expect(account.isAdmin).toBe(true);

		account.setViewingAsUser(true);

		// Every admin-gated view reads isAdmin, so this one flag is the preview...
		expect(account.isAdmin).toBe(false);
		// ...while the toggle and its banner still know who is really signed in.
		expect(account.isActuallyAdmin).toBe(true);

		account.setViewingAsUser(false);
		expect(account.isAdmin).toBe(true);
	});

	it('remembers the preview per session', () => {
		account.setViewingAsUser(true);
		expect(sessionStorage.getItem('almanac.viewAsUser')).toBe('1');

		account.setViewingAsUser(false);
		expect(sessionStorage.getItem('almanac.viewAsUser')).toBe('0');
	});

	it('cannot make a non-admin look like an admin', () => {
		account.account = asAdmin(false);

		expect(account.isActuallyAdmin).toBe(false);
		account.setViewingAsUser(false);
		expect(account.isAdmin).toBe(false);
	});
});
