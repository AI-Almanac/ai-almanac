import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({ getAccount: vi.fn(), patchSettings: vi.fn() }));

vi.mock('../src/lib/api', () => apiMocks);
vi.mock('../src/lib/api/core', () => ({ USE_BEARER_AUTH: false }));
vi.mock('../src/lib/auth', () => ({ hasApiAccessToken: () => false }));

import { account } from '../src/lib/account.svelte';
import type { SettingsField, SettingsGroup } from '../src/lib/api';
import { ConfigSettingsState } from '../src/lib/settings/config.svelte';

function adminWhoCanUseWorkspaces(canUseWorkspaces: boolean) {
	return {
		anonymous: false,
		id: 'u1',
		role: 'admin',
		deployment_mode: 'shared',
		capabilities: { can_admin: true, can_use_workspaces: canUseWorkspaces }
	};
}

const workspacesAudience = {
	name: 'workspaces_audience',
	type: 'string',
	choices: ['off', 'admins', 'everyone']
} as SettingsField;
const features: SettingsGroup = { name: 'Features', fields: [workspacesAudience] };

describe('saving a feature setting', () => {
	beforeEach(async () => {
		apiMocks.getAccount.mockReset();
		apiMocks.patchSettings.mockReset();
		apiMocks.getAccount.mockResolvedValue(adminWhoCanUseWorkspaces(true));
		await account.reload();
	});

	it('updates what the account can use without a page reload', async () => {
		const settings = new ConfigSettingsState(() => account.refresh());
		apiMocks.patchSettings.mockResolvedValue({
			values: { workspaces_audience: 'off' },
			secrets: {}
		});
		apiMocks.getAccount.mockResolvedValue(adminWhoCanUseWorkspaces(false));
		settings.setValue(workspacesAudience, 'off');

		await settings.saveGroup(features);

		expect(account.canUseWorkspaces).toBe(false);
	});

	it('keeps the settings page up while the account is read again', async () => {
		let finishRead: (value: unknown) => void = () => {};
		apiMocks.getAccount.mockReturnValue(new Promise((resolve) => (finishRead = resolve)));

		const refreshing = account.refresh();

		// The admin guard blanks the page whenever the account is not loaded.
		expect(account.loaded).toBe(true);
		expect(account.isAdmin).toBe(true);
		finishRead(adminWhoCanUseWorkspaces(false));
		await refreshing;
	});

	it('leaves the admin where they are when the account cannot be read again', async () => {
		apiMocks.getAccount.mockRejectedValue(new Error('GET /auth/me failed (502)'));

		await account.refresh();

		expect(account.isAdmin).toBe(true);
		expect(account.canUseWorkspaces).toBe(true);
	});

	it('does not read the account again when the save is refused', async () => {
		const settings = new ConfigSettingsState(() => account.refresh());
		apiMocks.patchSettings.mockRejectedValue(new Error('PATCH /settings failed (400)'));
		apiMocks.getAccount.mockClear();
		settings.setValue(workspacesAudience, 'off');

		await settings.saveGroup(features);

		expect(apiMocks.getAccount).not.toHaveBeenCalled();
		expect(settings.error).toContain('400');
	});
});
