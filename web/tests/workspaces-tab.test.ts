import { render, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const goto = vi.hoisted(() => vi.fn());
vi.mock('$app/navigation', () => ({ goto }));

const api = vi.hoisted(() => ({ listWorkspaces: vi.fn(), startBlankWorkspace: vi.fn() }));
vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

import { account } from '../src/lib/account.svelte';
import { rememberOpenedWorkspace } from '../src/lib/workspace/last-opened';
import WorkspacesTab from '../src/routes/workspaces/+page.svelte';

const listed = (id: string) => ({
	id,
	name: id,
	description: '',
	created_at: '2026-11-01T00:00:00Z',
	updated_at: '2026-11-01T00:00:00Z',
	untitled: false
});

function signIn() {
	account.account = {
		id: 'u1',
		subject: 'user',
		email: null,
		display_name: null,
		role: 'user',
		deployment_mode: 'shared',
		capabilities: {
			can_admin: false,
			can_browse_fs: false,
			can_manage_data: false,
			can_use_forecasting: false,
			can_use_workspaces: true,
			can_run_code: false
		}
	};
	account.loaded = true;
}

const stubStorage = () => {
	const items = new Map<string, string>();
	vi.stubGlobal('localStorage', {
		getItem: (key: string) => items.get(key) ?? null,
		setItem: (key: string, value: string) => void items.set(key, value)
	});
};

describe('the Workspaces tab', () => {
	beforeEach(() => {
		stubStorage();
		signIn();
	});

	afterEach(() => {
		account.account = null;
		account.loaded = false;
		vi.unstubAllGlobals();
		vi.clearAllMocks();
	});

	it('goes back to the workspace last open in this browser', async () => {
		rememberOpenedWorkspace(localStorage, 'u1', 'older');
		api.listWorkspaces.mockResolvedValue([listed('newest'), listed('older')]);

		render(WorkspacesTab);

		await waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/workspace/older', { replaceState: true })
		);
		expect(api.startBlankWorkspace).not.toHaveBeenCalled();
	});

	it('opens a blank workspace for someone who has none yet', async () => {
		api.listWorkspaces.mockResolvedValue([]);
		api.startBlankWorkspace.mockResolvedValue(listed('blank'));

		render(WorkspacesTab);

		await waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/workspace/blank', { replaceState: true })
		);
	});
});
