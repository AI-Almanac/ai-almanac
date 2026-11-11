import { fireEvent, render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
	getRegions: vi.fn(),
	createRegion: vi.fn(),
	updateRegion: vi.fn(),
	deleteRegion: vi.fn()
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/stores', async () => {
	const { readable } = await import('svelte/store');
	return { page: readable({ url: new URL('http://localhost/regions') }) };
});
vi.mock('$lib/account.svelte', () => ({
	account: { loaded: true, isAdmin: true, canManageData: true }
}));
vi.mock('$lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

import RegionsPage from '../src/routes/regions/+page.svelte';

async function fill(label: string, value: string) {
	await fireEvent.input(screen.getByLabelText(label), { target: { value } });
	await tick();
}

describe('regions page: create a region', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		api.getRegions.mockResolvedValue([]);
		api.createRegion.mockResolvedValue({ id: 'nigeria' });
	});

	it('enables Create region once the name and all four bounds are typed (number inputs)', async () => {
		render(RegionsPage);
		await tick();
		const button = screen.getByRole('button', { name: 'Create region' });
		expect((button as HTMLButtonElement).disabled).toBe(true);

		await fill('Name', 'Nigeria');
		await fill('South latitude', '3.75');
		await fill('North latitude', '14.5');
		await fill('West longitude', '2.25');
		expect((button as HTMLButtonElement).disabled).toBe(true); // (one bound still empty)
		await fill('East longitude', '15.25');
		expect((button as HTMLButtonElement).disabled).toBe(false);

		await fireEvent.click(button);
		await tick();
		expect(api.createRegion).toHaveBeenCalledWith({
			display_name: 'Nigeria',
			description: '',
			lat_min: 3.75,
			lat_max: 14.5,
			lon_min: 2.25,
			lon_max: 15.25,
			land_only: false
		});
	});

	it('disables it again when a bound is cleared', async () => {
		render(RegionsPage);
		await tick();
		await fill('Name', 'Nigeria');
		for (const [label, value] of [
			['South latitude', '3.75'],
			['North latitude', '14.5'],
			['West longitude', '2.25'],
			['East longitude', '15.25']
		]) {
			await fill(label, value);
		}
		const button = screen.getByRole('button', { name: 'Create region' });
		expect((button as HTMLButtonElement).disabled).toBe(false);
		await fill('North latitude', '');
		expect((button as HTMLButtonElement).disabled).toBe(true);
	});
});
