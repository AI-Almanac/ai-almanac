import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import WorkspaceSummaryStrip from '../src/lib/components/workspace/WorkspaceSummaryStrip.svelte';
import type { ForecastSummary, SeasonSummary, WorkspaceSummary } from '../src/lib/api';
import {
	canUpdateForecast,
	forecastFact,
	seasonFact,
	setupChips,
	skillFact,
	updateForecastPrompt
} from '../src/lib/workspace/summary';
import { formatDay } from '../src/lib/format/dates';

const season: SeasonSummary = {
	name: 'Kiremt',
	year: 2026,
	phase: 'in_season',
	first_issue_date: '2026-05-01',
	starts_label: 'onset search starts 1 May',
	last_issue_date: '2026-07-31',
	issued_through: '2026-07-09',
	days_until_start: null,
	next_season_start: null,
	is_default: false
};

const forecast: ForecastSummary = {
	kind: 'forecast',
	job_id: 'fc-1',
	status: 'complete',
	blended: true,
	blend_name: 'AIFS-FuXi Ethiopia Blend',
	first_issue_date: '2026-05-04',
	last_issue_date: '2026-07-09',
	issue_dates: 20,
	created_at: '2026-07-09T12:00:00Z'
};

const summary: WorkspaceSummary = {
	season,
	setup: { region: 'Ethiopia', observations: 'CHIRPS Ethiopia', models: ['aifs', 'fuxi'] },
	latest_forecast: forecast,
	skill: {
		kind: 'blend',
		job_id: 'blend-1',
		name: 'AIFS-FuXi Ethiopia Blend',
		rps_skill: 0.1152,
		brier_skill: 0.0486,
		holdout_years: '2019–2024'
	}
};

describe('summary words', () => {
	it('reads dates from the text so the day never shifts with the time zone', () => {
		expect(formatDay('2026-07-09')).toBe('9 Jul 2026');
		expect(formatDay('2026-05-01', false)).toBe('1 May');
	});

	it('says where the season stands in each phase', () => {
		expect(seasonFact(season).headline).toBe('Kiremt 2026 is under way');
		expect(seasonFact(season).detail).toBe('issued through 9 Jul · last issue date 31 Jul');
		expect(
			seasonFact({
				...season,
				year: 2027,
				phase: 'upcoming',
				first_issue_date: '2027-05-01',
				days_until_start: 173
			})
		).toEqual({ headline: 'Kiremt 2027 · onset search starts 1 May', detail: 'in 173 days' });
	});

	it('leads with the next season once the last one has ended', () => {
		const ended: SeasonSummary = { ...season, phase: 'ended', next_season_start: '2027-05-01' };

		expect(seasonFact(ended, new Date(2026, 10, 10))).toEqual({
			headline: 'Kiremt 2027 · onset search starts 1 May',
			detail: 'in 172 days'
		});
		expect(seasonFact(ended, new Date(2027, 3, 30, 23, 59)).detail).toBe('in 1 day');
		expect(seasonFact({ ...ended, name: 'Rainy season' }, new Date(2026, 10, 10)).headline).toBe(
			'Rainy season 2027 · onset search starts 1 May'
		);
	});

	it('still says a season ended when the next one is not known', () => {
		expect(seasonFact({ ...season, phase: 'ended', next_season_start: null })).toEqual({
			headline: 'Kiremt 2026 ended 31 Jul',
			detail: null
		});
	});

	it('names the latest forecast by its issue dates and blend, and only offers updates for blends', () => {
		expect(forecastFact(forecast)).toEqual({
			headline: 'Issued 4 May – 9 Jul 2026',
			detail: '20 issue dates · from AIFS-FuXi Ethiopia Blend'
		});
		expect(forecastFact(null).headline).toBe('No forecast yet');
		expect(forecastFact({ ...forecast, blended: false }).headline).toBe('Raw model forecast');
		expect(canUpdateForecast(forecast)).toBe(true);
		expect(canUpdateForecast({ ...forecast, blended: false })).toBe(false);
		expect(canUpdateForecast({ ...forecast, status: 'running' })).toBe(false);
		expect(updateForecastPrompt(forecast)).toContain('AIFS-FuXi Ethiopia Blend');
	});

	it('leads the skill fact with the number, for a blend or a benchmark', () => {
		expect(skillFact(summary.skill)).toEqual({
			headline: 'RPSS 0.12 vs climatology',
			detail: 'AIFS-FuXi Ethiopia Blend · held out 2019–2024'
		});
		expect(
			skillFact({
				kind: 'benchmark',
				job_id: 'job-1',
				model: 'aifs',
				mae_days: 6.4,
				climatology_mae_days: 11.9,
				window: '1-15'
			})
		).toEqual({
			headline: 'AIFS · 6.4 d onset error',
			detail: 'best model, days 1–15 · climatology 11.9 d'
		});
		expect(skillFact(null).headline).toBe('Not benchmarked yet');
	});

	it('shows the setup as chips with model display names, leaving unknowns out', () => {
		expect(setupChips(summary.setup)).toEqual(['Ethiopia', 'CHIRPS Ethiopia', 'AIFS, FuXi']);
		expect(setupChips({ region: null, observations: null, models: [] })).toEqual([]);
	});
});

describe('WorkspaceSummaryStrip', () => {
	it('opens the run behind a fact and asks the assistant to update the forecast', async () => {
		const onOpenJob = vi.fn();
		const onUpdateForecast = vi.fn();
		render(WorkspaceSummaryStrip, { summary, canAct: true, onOpenJob, onUpdateForecast });

		await fireEvent.click(screen.getByRole('button', { name: 'RPSS 0.12 vs climatology' }));
		expect(onOpenJob).toHaveBeenCalledWith('blend-1');

		await fireEvent.click(screen.getByRole('button', { name: 'Update forecast' }));
		expect(onUpdateForecast).toHaveBeenCalled();
		expect(screen.getByText(/Kiremt 2026 is under way/)).toBeTruthy();
		expect(screen.getByText('Ethiopia · CHIRPS Ethiopia · AIFS, FuXi')).toBeTruthy();
		expect(screen.getByTitle('AIFS-FuXi Ethiopia Blend · held out 2019–2024')).toBeTruthy();
	});

	it('offers no update when the assistant is not there, and notes a default season', () => {
		render(WorkspaceSummaryStrip, {
			summary: { ...summary, season: { ...season, is_default: true } },
			canAct: false
		});

		expect(screen.queryByRole('button', { name: 'Update forecast' })).toBeNull();
		expect(screen.getByText('(default dates)').title).toMatch(/Platform default dates/);
	});
});
