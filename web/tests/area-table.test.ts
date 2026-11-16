import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { ArtifactProvenance } from '../src/lib/api';
import AreaTable from '../src/lib/components/workspace/AreaTable.svelte';
import {
	parseAreaTable,
	percent,
	rowsOn,
	valuesAreShares,
	valuesNote,
	windowLabel
} from '../src/lib/workspace/area-table';
import { formatBytes } from '../src/lib/workspace/file-preview';
import { nextSort, sortRows } from '../src/lib/workspace/table-sort';

const CSV = [
	'issue_date,area,parent,p_within_7d,p_within_14d,most_likely,most_likely_share,coverage',
	'2026-06-01,Awi,Amhara,0.2,0.5,week2,0.4,0.9',
	'2026-06-01,Bale,Oromia,,0.7,week1,0.6,',
	'2026-06-01,,Oromia,0.1,0.1,week1,0.1,1',
	',Guji,Oromia,0.3,0.3,week1,0.3,1',
	'2026-06-08,Awi,Amhara,not-a-number,0.6,week1,0.5,0.9'
].join('\n');

describe('an area summary table', () => {
	const table = parseAreaTable(CSV);

	it('skips a row with no issue date or no area, and keeps the rest by date', () => {
		expect(table.issueDates).toEqual(['2026-06-01', '2026-06-08']);
		expect(table.rowsByDate.get('2026-06-01')?.map((row) => row.area)).toEqual(['Awi', 'Bale']);
		expect(table.columns.map((column) => column.days)).toEqual([7, 14]);
	});

	it('reads an empty or non-numeric cell as no value', () => {
		const [bale] = table.rowsByDate.get('2026-06-01')!.filter((row) => row.area === 'Bale');
		expect(bale.within.p_within_7d).toBeNull();
		expect(bale.coverage).toBeNull();
		expect(table.rowsByDate.get('2026-06-08')![0].within.p_within_7d).toBeNull();
	});

	it('puts a row without a value last whichever way it is sorted', () => {
		for (const direction of ['ascending', 'descending'] as const) {
			const rows = rowsOn(table, '2026-06-01', { key: 'p_within_7d', direction });
			expect(rows.at(-1)?.area).toBe('Bale');
		}
	});

	it('words its windows and percentages for a reader', () => {
		expect(windowLabel('week2')).toBe('Week 2');
		expect(windowLabel('day_8')).toBe('Day 8');
		expect(windowLabel(null)).toBe('–');
		expect(percent(0.456)).toBe('46%');
		expect(percent(null)).toBe('–');
	});

	it('shows two areas of the same name as two rows', () => {
		const twice = [
			'issue_date,area,p_within_14d',
			'2026-06-01,Central,0.2',
			'2026-06-01,Central,0.4'
		].join('\n');
		render(AreaTable, { text: twice, shares: false });

		expect(screen.getAllByText('Central')).toHaveLength(2);
	});
});

describe('how an area table’s values read', () => {
	const steps = (single_run?: boolean) =>
		({
			steps: [
				{
					primitive: 'forecast.summarize_by_area',
					params: single_run === undefined ? {} : { single_run }
				}
			]
		}) as unknown as ArtifactProvenance;

	it('is unknown until the file’s history is read', () => {
		expect(valuesAreShares(null)).toBeNull();
		expect(valuesNote(null)).not.toMatch(/chance/);
	});

	it('is one run’s shares for a single-run summary, and chances otherwise', () => {
		expect(valuesAreShares(steps(true))).toBe(true);
		expect(valuesAreShares(steps())).toBe(false);
		expect(valuesNote(true)).toMatch(/one run, not a chance/);
		expect(valuesNote(false)).toMatch(/chance of onset/);
	});
});

describe('sorting a table by a column', () => {
	it('goes first direction, then the other, then back to the table’s own order', () => {
		const first = nextSort(null, 'p');
		const second = nextSort(first, 'p');
		expect(first).toEqual({ key: 'p', direction: 'descending' });
		expect(second).toEqual({ key: 'p', direction: 'ascending' });
		expect(nextSort(second, 'p')).toBeNull();
	});

	it('keeps rows without a value last and ties in their order', () => {
		const rows = [{ v: 2 }, { v: null }, { v: 1 }, { v: 2 }];
		const sorted = sortRows(rows, { key: 'v', direction: 'descending' }, (row) => row.v);
		expect(sorted.map((row) => row.v)).toEqual([2, 2, 1, null]);
		expect(sorted[0]).toBe(rows[0]);
	});
});

describe('a file size', () => {
	it('moves up a unit rather than showing 1000 of one', () => {
		expect(formatBytes(999)).toBe('999 B');
		expect(formatBytes(999_999)).toBe('1.0 MB');
		expect(formatBytes(1_500_000)).toBe('1.5 MB');
	});
});
