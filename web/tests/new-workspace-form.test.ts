import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { SeasonWindow } from '../src/lib/api/workspace-settings';

vi.mock('../src/lib/components/FocusAreaMap.svelte', async () => ({
	default: (await import('./fixtures/FocusAreaMapStub.svelte')).default
}));

import NewWorkspaceForm from '../src/lib/components/workspace/NewWorkspaceForm.svelte';

const rainy: SeasonWindow = {
	name: 'Rainy season',
	onset_search_start: '05-01',
	reference_onset: '06-01',
	last_issue_date: '07-31'
};
const kiremt: SeasonWindow = { ...rainy, name: 'Kiremt', reference_onset: '06-15' };

function renderForm() {
	const usualSeason = vi.fn(async (country: string | null) => (country === 'ETH' ? kiremt : rainy));
	const onCreate = vi.fn().mockResolvedValue(undefined);
	render(NewWorkspaceForm, { usualSeason, onCreate, onCancel: vi.fn() });
	return { usualSeason, onCreate };
}

async function named(name: string) {
	await fireEvent.input(screen.getByRole('textbox', { name: 'Name' }), { target: { value: name } });
}

const create = () => fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));

describe('making a workspace', () => {
	it('leaves area and season to the platform when neither is chosen', async () => {
		const { onCreate } = renderForm();
		await named('Kiremt 2026');

		await create();

		expect(onCreate).toHaveBeenCalledWith('Kiremt 2026', '', undefined);
	});

	it('shows the season usual where the chosen places are, and keeps the area', async () => {
		const { onCreate } = renderForm();
		await named('Amhara 2026');

		await fireEvent.click(screen.getByRole('button', { name: 'Pick Amhara, Ethiopia' }));

		expect(await screen.findByText(/Kiremt/)).toBeTruthy();
		await create();
		expect(onCreate).toHaveBeenCalledWith('Amhara 2026', '', {
			study_area: { level: 'adm1', units: ['Amhara'], iso: 'ETH' }
		});
	});

	it('makes the workspace with its own dates when they are set', async () => {
		const { onCreate } = renderForm();
		await named('Belg 2027');
		const own = screen.getByRole('radio', { name: 'Set my own dates' });
		await waitFor(() => expect((own as HTMLInputElement).disabled).toBe(false));

		await fireEvent.click(own);
		await fireEvent.input(screen.getByRole('textbox', { name: 'Season name' }), {
			target: { value: 'Belg' }
		});
		await create();

		expect(onCreate).toHaveBeenCalledWith('Belg 2027', '', { season: { ...rainy, name: 'Belg' } });
	});

	it('does not make a workspace whose own season has no name', async () => {
		const { onCreate } = renderForm();
		await named('Unnamed season');
		const own = screen.getByRole('radio', { name: 'Set my own dates' });
		await waitFor(() => expect((own as HTMLInputElement).disabled).toBe(false));

		await fireEvent.click(own);
		await fireEvent.input(screen.getByRole('textbox', { name: 'Season name' }), {
			target: { value: ' ' }
		});
		await create();

		expect(onCreate).not.toHaveBeenCalled();
	});
});
