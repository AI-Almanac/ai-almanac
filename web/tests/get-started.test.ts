import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { account } from '../src/lib/account.svelte';
import MainContent from '../src/routes/MainContent.svelte';

function setForecasting(canUseForecasting: boolean) {
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
			can_use_forecasting: canUseForecasting,
			can_run_code: false
		}
	};
}

async function openGetStarted() {
	render(MainContent);
	await fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
}

describe('Get started dialog', () => {
	it('offers every workflow, including blends and forecasts', async () => {
		setForecasting(true);
		await openGetStarted();
		for (const title of [
			'Browse the almanac',
			'Benchmark models',
			'Blend models',
			'Run a forecast'
		]) {
			expect(screen.getByText(title)).toBeTruthy();
		}
	});

	it('leaves out forecasts for accounts without forecasting', async () => {
		setForecasting(false);
		await openGetStarted();
		expect(screen.getByText('Blend models')).toBeTruthy();
		expect(screen.queryByText('Run a forecast')).toBeNull();
	});
});
