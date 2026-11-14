import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../src/lib/api/core';
import type { FocusAreaValue } from '../src/lib/api/jobs';
import type { SeasonWindow } from '../src/lib/api/workspace-settings';

vi.mock('../src/lib/components/FocusAreaMap.svelte', async () => ({
	default: (await import('./fixtures/FocusAreaMapStub.svelte')).default
}));

import AreaControl from '../src/lib/components/workspace/AreaControl.svelte';
import SeasonControl from '../src/lib/components/workspace/SeasonControl.svelte';

const kiremt: SeasonWindow = {
	name: 'Kiremt',
	onset_search_start: '05-01',
	reference_onset: '06-15',
	last_issue_date: '07-31'
};

const amhara: FocusAreaValue = { level: 'adm1', units: ['Amhara'] };

describe('the season in the workspace header', () => {
	function renderSeason(load = vi.fn().mockResolvedValue(kiremt), isDefault = false) {
		const save = vi.fn().mockResolvedValue(undefined);
		render(SeasonControl, { headline: 'Kiremt 2026 is under way', isDefault, load, save });
		return { load, save };
	}

	const trigger = () => screen.getByRole('button', { name: /Kiremt 2026/ });

	it('says when the platform’s dates apply', () => {
		renderSeason(undefined, true);

		expect(trigger().textContent).toContain('(default dates)');
	});

	it('opens the season in force to edit, read afresh each time', async () => {
		const { load } = renderSeason();

		await fireEvent.click(trigger());

		expect(trigger().getAttribute('aria-expanded')).toBe('true');
		expect(screen.getByRole('dialog', { name: 'Season' })).toBeTruthy();
		expect(await screen.findByRole('button', { name: 'Save' })).toBeTruthy();

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(screen.queryByRole('dialog')).toBeNull();
		await fireEvent.click(trigger());
		expect(load).toHaveBeenCalledTimes(2);
	});

	it('says so when the season cannot be read', async () => {
		renderSeason(vi.fn().mockRejectedValue(new Error('offline')));

		await fireEvent.click(trigger());

		expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
	});
});

describe('the area in the workspace header', () => {
	function renderArea(save = vi.fn().mockResolvedValue(undefined)) {
		render(AreaControl, { area: amhara, save });
		return { save };
	}

	const trigger = () => screen.getByRole('button', { name: /Amhara/ });

	it('names the area runs here are scored in', () => {
		renderArea();

		expect(trigger().textContent).toContain('Amhara');
	});

	it('starts the map from the workspace’s area and saves the area picked', async () => {
		const { save } = renderArea();

		await fireEvent.click(trigger());
		expect(JSON.parse(screen.getByTestId('map-area').textContent ?? '')).toEqual(amhara);
		await fireEvent.click(screen.getByRole('button', { name: 'Pick Tigray' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Save' }));

		expect(save).toHaveBeenCalledWith({ level: 'adm1', units: ['Tigray'] });
		await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
	});

	it('keeps the map open with the server’s reason when the area is refused', async () => {
		renderArea(vi.fn().mockRejectedValue(new ApiError(422, 'No such area', 'PATCH failed')));

		await fireEvent.click(trigger());
		await fireEvent.click(screen.getByRole('button', { name: 'Save' }));

		expect((await screen.findByRole('alert')).textContent).toBe('No such area');
		expect(screen.getByRole('dialog', { name: 'Area' })).toBeTruthy();
	});

	it('drops an unsaved pick when it is opened again', async () => {
		const { save } = renderArea();

		await fireEvent.click(trigger());
		await fireEvent.click(screen.getByRole('button', { name: 'Pick Tigray' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		await fireEvent.click(trigger());

		expect(JSON.parse(screen.getByTestId('map-area').textContent ?? '')).toEqual(amhara);
		expect(save).not.toHaveBeenCalled();
	});
});
