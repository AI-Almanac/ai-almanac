import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AreaTable from '../src/lib/components/workspace/AreaTable.svelte';
import SkillByArea from '../src/lib/components/workspace/SkillByArea.svelte';
import { ApiError } from '../src/lib/api/core';
import { skillByArea } from './fixtures/skill-by-area';

const skillApi = vi.hoisted(() => ({ getSkillByArea: vi.fn() }));
vi.mock('../src/lib/api/skill-by-area', () => skillApi);

import type { ArtifactProvenance } from '../src/lib/api';
import {
	defaultSortKey,
	isAreaSummary,
	latestIssueDate,
	parseAreaTable,
	percent,
	rowsOn,
	valuesAreShares,
	windowLabel
} from '../src/lib/workspace/area-table';

const CSV = `issue_date,area,parent,points,coverage,p_week1,p_week2,p_later,p_within_7d,p_within_14d,p_within_28d,most_likely,most_likely_share
2024-05-02,West Wellega,Oromia,28,1.0,0.0,0.3,0.7,0.0,0.462,0.657,later,0.343
2024-05-02,Bale,Oromia,12,0.9,0.1,0.6,0.3,0.1,0.8,0.9,week2,0.587
2024-05-06,West Wellega,Oromia,28,1.0,0.2,0.4,0.4,0.2,0.631,0.635,week1,0.5
`;

describe('area table parsing', () => {
	it('reads the within columns, groups rows by issue date and knows the latest', () => {
		const table = parseAreaTable(CSV);
		expect(table.columns.map((c) => c.label)).toEqual(['Within 7 d', 'Within 14 d', 'Within 28 d']);
		expect(table.issueDates).toEqual(['2024-05-02', '2024-05-06']);
		expect(latestIssueDate(table)).toBe('2024-05-06');
		expect(defaultSortKey(table)).toBe('p_within_14d');
	});

	it('orders a date’s rows by a column, highest first, or by name', () => {
		const table = parseAreaTable(CSV);
		const byChance = rowsOn(table, '2024-05-02', { key: 'p_within_14d', direction: 'descending' });
		expect(byChance.map((r) => r.area)).toEqual(['Bale', 'West Wellega']);
		const byName = rowsOn(table, '2024-05-02', { key: 'area', direction: 'ascending' });
		expect(byName.map((r) => r.area)).toEqual(['Bale', 'West Wellega']);
		expect(rowsOn(table, null, { key: 'area', direction: 'ascending' })).toEqual([]);
	});

	it.each([
		['week2', 'Week 2'],
		['day_8', 'Day 8'],
		['later', 'Later']
	])('names the %s window "%s"', (window, label) => {
		expect(windowLabel(window)).toBe(label);
	});

	it('speaks values plainly', () => {
		expect(percent(0.462)).toBe('46%');
		expect(percent(null)).toBe('–');
	});

	it('recognises an area summary by the step that wrote it, and single-run shares by its record', () => {
		expect(
			isAreaSummary({ kind: 'table', breadcrumb: ['forecast.job', 'forecast.summarize_by_area'] })
		).toBe(true);
		expect(
			isAreaSummary({
				kind: 'figure',
				breadcrumb: ['forecast.summarize_by_area', 'figure.plot_area_map']
			})
		).toBe(false);
		const provenance = {
			steps: [{ primitive: 'forecast.summarize_by_area', params: { single_run: true } }]
		} as unknown as ArtifactProvenance;
		expect(valuesAreShares(provenance)).toBe(true);
		expect(valuesAreShares(null)).toBe(false);
	});
});

describe('AreaTable', () => {
	it('shows the latest issue date first, switches dates, and says what the values mean', async () => {
		render(AreaTable, { text: CSV, shares: true });

		const rows = () => within(screen.getByRole('table')).getAllByRole('row').slice(1);
		expect(rows()).toHaveLength(1);
		expect(rows()[0].textContent).toContain('West Wellega');
		expect(rows()[0].textContent).toContain('63%');
		expect(screen.getByText(/share of each area/)).toBeTruthy();

		await fireEvent.change(screen.getByRole('combobox'), { target: { value: '2024-05-02' } });
		expect(rows()).toHaveLength(2);
		expect(rows()[0].textContent).toContain('Bale');

		await fireEvent.click(screen.getByRole('button', { name: 'All columns' }));
		expect(screen.getByText('most_likely_share')).toBeTruthy();
	});
});

describe('a benchmark’s skill by zone', () => {
	beforeEach(() => {
		skillApi.getSkillByArea.mockReset().mockResolvedValue(skillByArea());
	});

	function renderZones() {
		return render(SkillByArea, { workspaceId: 'ws-1', runKey: 'run-1' });
	}

	const bodyRows = () => within(screen.getByRole('table')).getAllByRole('row').slice(1);
	// The best value is said, not only shown in bold, so a screen reader hears it too.
	const bestIn = (row: HTMLElement) =>
		within(row)
			.getAllByRole('cell')
			.map((cell) => cell.textContent?.trim() ?? '')
			.filter((text) => text.endsWith(', best'))
			.map((text) => text.replace(/, best$/, ''));

	it('asks for the run’s own areas and opens on onset error for every model and the baseline', async () => {
		renderZones();

		expect(screen.getByRole('status').textContent).toContain('Working out skill by area');
		await screen.findByRole('table');
		expect(skillApi.getSkillByArea).toHaveBeenCalledWith('ws-1', 'run-1', {});
		expect(
			screen.getAllByRole('columnheader').map((heading) => heading.textContent?.trim())
		).toEqual(['Area', 'AIFS', 'FuXi', 'Climatology']);
		expect(screen.getByRole('button', { name: 'Onset error' }).getAttribute('aria-pressed')).toBe(
			'true'
		);

		const west = bodyRows()[1];
		expect(west.textContent).toContain('West Gojjam');
		expect(west.textContent).toContain('8–12 cells');
		expect(bestIn(west)).toEqual(['4.8 d']);
		// Climatology beats both models in East Gojjam, and is marked.
		expect(bestIn(bodyRows()[0])).toEqual(['5.0 d']);
	});

	it('shows one measure at a time, so the models fit side by side', async () => {
		renderZones();
		await screen.findByRole('table');

		await fireEvent.click(screen.getByRole('button', { name: 'False alarms' }));
		expect(bestIn(bodyRows()[1])).toEqual(['25%']);

		await fireEvent.click(screen.getByRole('button', { name: 'Misses' }));
		expect(bestIn(bodyRows()[1])).toEqual(['10%']);
		expect(skillApi.getSkillByArea).toHaveBeenCalledTimes(1);
	});

	it('sorts the areas by a model’s column, worst first, then best first, then as they came', async () => {
		renderZones();
		await screen.findByRole('table');
		const areas = () => bodyRows().map((row) => row.textContent?.trim().split(/\s/)[0]);
		expect(areas()).toEqual(['East', 'West', 'Wag']);

		await fireEvent.click(screen.getByRole('button', { name: 'FuXi' }));
		expect(areas()).toEqual(['East', 'West', 'Wag']);
		expect(screen.getByRole('columnheader', { name: /FuXi/ }).getAttribute('aria-sort')).toBe(
			'descending'
		);

		await fireEvent.click(screen.getByRole('button', { name: 'FuXi' }));
		expect(areas()).toEqual(['West', 'East', 'Wag']);

		await fireEvent.click(screen.getByRole('button', { name: 'FuXi' }));
		expect(areas()).toEqual(['East', 'West', 'Wag']);
		const sortedBy = screen.getByRole('columnheader', { name: /FuXi/ }).getAttribute('aria-sort');
		expect(['ascending', 'descending']).not.toContain(sortedBy);
	});

	it('lists an area the run did not cover instead of leaving it out', async () => {
		renderZones();
		await screen.findByRole('table');

		const last = bodyRows().at(-1)!;
		expect(last.textContent).toContain('Wag Hemra');
		expect(last.textContent).toContain('Not covered by this run');
	});

	it('switches lead window without asking again', async () => {
		renderZones();
		await screen.findByRole('table');

		await fireEvent.click(screen.getByRole('button', { name: 'Days 16–30' }));

		expect(bodyRows()[0].textContent).toContain('11.1 d');
		expect(skillApi.getSkillByArea).toHaveBeenCalledTimes(1);
	});

	it('asks again at another administrative level', async () => {
		renderZones();
		await screen.findByRole('table');

		await fireEvent.change(screen.getByRole('combobox', { name: 'Areas' }), {
			target: { value: 'adm1' }
		});

		await waitFor(() =>
			expect(skillApi.getSkillByArea).toHaveBeenLastCalledWith('ws-1', 'run-1', { level: 'adm1' })
		);
	});

	it('says in the server’s own words why there is no table', async () => {
		const why = 'This benchmark has not finished, so it has no skill by area yet';
		skillApi.getSkillByArea.mockRejectedValue(new ApiError(400, why, 'Bad request'));
		renderZones();

		expect((await screen.findByRole('alert')).textContent).toContain(why);
		expect(screen.queryByRole('table')).toBeNull();
	});

	it('says so when the run has no areas at all', async () => {
		skillApi.getSkillByArea.mockResolvedValue({ ...skillByArea(), rows: [], not_covered: [] });
		renderZones();

		expect(await screen.findByText('This run has no areas to show skill for.')).toBeTruthy();
	});
});
