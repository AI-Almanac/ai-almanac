import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import { ApiError, type WorkspaceSummary } from '../src/lib/api';
import type { SeasonWindow } from '../src/lib/api/workspace-settings';
import WorkspaceSummaryStrip from '../src/lib/components/workspace/WorkspaceSummaryStrip.svelte';
import {
	daysIn,
	formatMonthDay,
	parseMonthDay,
	seasonDraft,
	seasonFromDraft,
	seasonProblems
} from '../src/lib/workspace/season-form';

const kiremt: SeasonWindow = {
	name: 'Kiremt',
	onset_search_start: '05-01',
	reference_onset: '06-01',
	last_issue_date: '07-31'
};

const summary: WorkspaceSummary = {
	season: {
		name: 'Kiremt',
		year: 2026,
		phase: 'in_season',
		first_issue_date: '2026-05-01',
		starts_label: 'onset search starts 1 May',
		last_issue_date: '2026-07-31',
		issued_through: null,
		days_until_start: null,
		next_season_start: null,
		is_default: false
	},
	setup: { region: 'Ethiopia', observations: 'CHIRPS Ethiopia', models: ['aifs'] },
	latest_forecast: null,
	skill: null
};

function refusal(detail: string): ApiError {
	return new ApiError(422, detail, detail);
}

describe('season dates', () => {
	it('reads a stored date as a month and a day, and writes it back the same', () => {
		expect(parseMonthDay('07-31')).toEqual({ month: 7, day: 31 });
		expect(formatMonthDay({ month: 7, day: 31 })).toBe('07-31');
		expect(seasonFromDraft(seasonDraft(kiremt))).toEqual(kiremt);
	});

	it('offers only days every year has, and holds a day to its month', () => {
		expect(daysIn(2).at(-1)).toBe(28);
		expect(formatMonthDay({ month: 4, day: 31 })).toBe('04-30');
	});
});

describe('a refused season, in plain words', () => {
	it('puts a rule about two dates beside the date it is about, with dates as spoken', () => {
		const problems = seasonProblems(
			'Invalid workspace settings: season: the reference onset date 04-01 is before the onset ' +
				'search start 05-01; onset cannot be found before the search begins'
		);

		expect(problems).toEqual({
			fields: {
				reference_onset:
					'The reference onset date 1 April is before the onset search start 1 May; onset cannot be found before the search begins.'
			},
			general: null
		});
	});

	it('splits a refusal that names more than one field', () => {
		const problems = seasonProblems(
			'Invalid workspace settings: season.name: Input should be a valid string; ' +
				"season.last_issue_date: must be a calendar date in MM-DD form (e.g. 05-01, not Feb 29), got '02-30'"
		);

		expect(problems.fields).toEqual({
			name: 'Input should be a valid string.',
			last_issue_date: 'Choose a date that exists in every year.'
		});
	});

	it('shows anything else as it was said', () => {
		expect(seasonProblems('Workspace not found')).toEqual({
			fields: {},
			general: 'Workspace not found'
		});
	});
});

describe('the season in the workspace header', () => {
	const openForm = async (editor: {
		load: () => Promise<SeasonWindow>;
		save: (season: SeasonWindow | null) => Promise<void>;
	}) => {
		render(WorkspaceSummaryStrip, { summary, seasonEditor: editor });
		await fireEvent.click(screen.getByRole('button', { name: /Kiremt 2026/ }));
		return within(await screen.findByRole('dialog', { name: 'Season' }));
	};

	it('opens a form holding the season in force and saves what is changed', async () => {
		const save = vi.fn().mockResolvedValue(undefined);
		const form = await openForm({ load: async () => kiremt, save });

		const name = await form.findByRole('textbox', { name: 'Season name' });
		expect((name as HTMLInputElement).value).toBe('Kiremt');
		await fireEvent.input(name, { target: { value: 'Belg' } });
		await fireEvent.change(form.getByRole('combobox', { name: 'Last issue date: day' }), {
			target: { value: '15' }
		});
		await fireEvent.click(form.getByRole('button', { name: 'Save' }));

		expect(save).toHaveBeenCalledWith({ ...kiremt, name: 'Belg', last_issue_date: '07-15' });
		await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: 'Season' })).toBeNull());
	});

	it('keeps the form open with the refusal beside the date it is about', async () => {
		const save = vi
			.fn()
			.mockRejectedValue(
				refusal(
					'Invalid workspace settings: season: the last issue date 04-30 must come after the ' +
						'onset search start 05-01; a season that crosses the new year is not supported yet'
				)
			);
		const form = await openForm({ load: async () => kiremt, save });

		await fireEvent.click(await form.findByRole('button', { name: 'Save' }));

		const problem = await form.findByText(/The last issue date 30 April must come after/);
		expect(problem.closest('fieldset')?.textContent).toContain('Last issue date');
		expect(screen.getByRole('dialog', { name: 'Season' })).toBeTruthy();
	});

	it('asks for a name before saving', async () => {
		const save = vi.fn();
		const form = await openForm({ load: async () => kiremt, save });

		await fireEvent.input(await form.findByRole('textbox', { name: 'Season name' }), {
			target: { value: '  ' }
		});
		await fireEvent.click(form.getByRole('button', { name: 'Save' }));

		expect(form.getByText('Give the season a name.')).toBeTruthy();
		expect(save).not.toHaveBeenCalled();
	});

	it('goes back to the default dates, and closes on Cancel without saving', async () => {
		const save = vi.fn().mockResolvedValue(undefined);
		const form = await openForm({ load: async () => kiremt, save });

		await fireEvent.click(await form.findByRole('button', { name: 'Use the default dates' }));
		expect(save).toHaveBeenCalledWith(null);
		await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: 'Season' })).toBeNull());

		await fireEvent.click(screen.getByRole('button', { name: /Kiremt 2026/ }));
		await fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
		expect(screen.queryByRole('dialog', { name: 'Season' })).toBeNull();
		expect(save).toHaveBeenCalledTimes(1);
	});

	it('is plain text where the season cannot be changed', () => {
		render(WorkspaceSummaryStrip, { summary });

		expect(screen.queryByRole('button', { name: /Kiremt 2026/ })).toBeNull();
	});
});
