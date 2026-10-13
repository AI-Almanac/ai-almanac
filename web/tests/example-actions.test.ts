import { render, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../src/lib/api', async (importOriginal) => ({
	...(await importOriginal<typeof import('../src/lib/api')>()),
	promoteJobToExample: vi.fn(async () => ({})),
	unshareJob: vi.fn(async () => ({}))
}));

import { account } from '../src/lib/account.svelte';
import { promoteJobToExample, unshareJob } from '../src/lib/api';
import ExampleActions from '../src/lib/components/ExampleActions.svelte';

function setAccount(canAdmin: boolean) {
	account.account = {
		id: 'u1',
		subject: 'boss',
		email: null,
		display_name: null,
		role: canAdmin ? 'admin' : 'user',
		deployment_mode: 'shared',
		capabilities: {
			can_admin: canAdmin,
			can_browse_fs: false,
			can_manage_data: false,
			can_use_forecasting: true,
			can_run_code: false
		}
	};
	account.setViewingAsUser(false);
}

describe('ExampleActions', () => {
	afterEach(() => {
		account.account = null;
		account.setViewingAsUser(false);
	});

	beforeEach(() => {
		vi.clearAllMocks();
		vi.spyOn(window, 'confirm').mockReturnValue(true);
	});

	it('renders nothing for non-admins', () => {
		setAccount(false);
		const { queryByRole } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1'], isExample: false, onChanged: () => {} }
		});
		expect(queryByRole('button')).toBeNull();
	});

	it('promotes a completed job after confirmation', async () => {
		setAccount(true);
		const onChanged = vi.fn();
		const { getByRole } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1'], isExample: false, onChanged }
		});

		const button = getByRole('button', { name: 'Feature as example' });
		button.click();
		await waitFor(() => expect(onChanged).toHaveBeenCalled());
		expect(promoteJobToExample).toHaveBeenCalledWith('job-1');
	});

	it('demotes every job in the group', async () => {
		setAccount(true);
		const onChanged = vi.fn();
		const { getByRole } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1', 'job-2'], isExample: true, onChanged }
		});

		getByRole('button', { name: 'Stop featuring' }).click();
		await waitFor(() => expect(onChanged).toHaveBeenCalled());
		expect(unshareJob).toHaveBeenCalledTimes(2);
		expect(unshareJob).toHaveBeenCalledWith('job-1');
		expect(unshareJob).toHaveBeenCalledWith('job-2');
	});

	it('does nothing when the confirmation is declined', async () => {
		setAccount(true);
		vi.spyOn(window, 'confirm').mockReturnValue(false);
		const onChanged = vi.fn();
		const { getByRole } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1'], isExample: false, onChanged }
		});

		getByRole('button', { name: 'Feature as example' }).click();
		expect(promoteJobToExample).not.toHaveBeenCalled();
		expect(onChanged).not.toHaveBeenCalled();
	});
});
