import { render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { account } from '../src/lib/account.svelte';
import OpenInWorkspace from '../src/lib/components/OpenInWorkspace.svelte';

function signInWith(canUseWorkspaces: boolean) {
	account.account = {
		id: 'u1',
		subject: 'forecaster',
		email: null,
		display_name: null,
		role: 'user',
		deployment_mode: 'shared',
		capabilities: {
			can_admin: false,
			can_browse_fs: false,
			can_manage_data: false,
			can_use_forecasting: true,
			can_use_workspaces: canUseWorkspaces,
			can_run_code: false
		}
	};
}

describe('Open in workspace', () => {
	afterEach(() => {
		account.account = null;
	});

	it('is offered to someone who can use workspaces', () => {
		signInWith(true);
		render(OpenInWorkspace, { sessionId: 'chat-1' });

		expect(screen.getByRole('button', { name: 'Open in workspace' })).toBeTruthy();
	});

	it('is not offered to someone workspaces are not open to', () => {
		signInWith(false);
		render(OpenInWorkspace, { sessionId: 'chat-1' });

		expect(screen.queryByRole('button', { name: 'Open in workspace' })).toBeNull();
	});
});
