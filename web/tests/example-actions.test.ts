import { render } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

async function flush() {
	// The demote path awaits one API call per job; give the microtask queue
	// enough turns to drain the whole chain.
	for (let i = 0; i < 8; i++) await Promise.resolve();
}

describe('ExampleActions', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.spyOn(window, 'confirm').mockReturnValue(true);
	});

	it('renders nothing for non-admins', () => {
		setAccount(false);
		const { container } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1'], isExample: false, onChanged: () => {} }
		});
		expect(container.querySelector('button')).toBeNull();
	});

	it('promotes a completed job after confirmation', async () => {
		setAccount(true);
		const onChanged = vi.fn();
		const { getByRole } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1'], isExample: false, onChanged }
		});

		const button = getByRole('button', { name: 'Feature as example' });
		button.click();
		await flush();

		expect(promoteJobToExample).toHaveBeenCalledWith('job-1');
		expect(onChanged).toHaveBeenCalled();
	});

	it('demotes every job in the group', async () => {
		setAccount(true);
		const onChanged = vi.fn();
		const { getByRole } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1', 'job-2'], isExample: true, onChanged }
		});

		getByRole('button', { name: 'Stop featuring' }).click();
		await flush();

		expect(unshareJob).toHaveBeenCalledTimes(2);
		expect(unshareJob).toHaveBeenCalledWith('job-1');
		expect(unshareJob).toHaveBeenCalledWith('job-2');
		expect(onChanged).toHaveBeenCalled();
	});

	it('does nothing when the confirmation is declined', async () => {
		setAccount(true);
		vi.spyOn(window, 'confirm').mockReturnValue(false);
		const onChanged = vi.fn();
		const { getByRole } = render(ExampleActions, {
			props: { promoteId: 'job-1', demoteIds: ['job-1'], isExample: false, onChanged }
		});

		getByRole('button', { name: 'Feature as example' }).click();
		await flush();

		expect(promoteJobToExample).not.toHaveBeenCalled();
		expect(onChanged).not.toHaveBeenCalled();
	});
});
