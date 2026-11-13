import { describe, expect, it } from 'vitest';

import {
	cellsWords,
	measureRows,
	missingModelsNote,
	zoneNote,
	zoneTable,
	zoneWindow
} from '../src/lib/workspace/skill-by-area';
import { skillByArea, skillRow } from './fixtures/skill-by-area';

describe('skill by area as a table', () => {
	it('opens on the lead window asked for, else the first', () => {
		const data = skillByArea();
		expect(zoneWindow(data, '16-30')).toBe('16-30');
		expect(zoneWindow(data, 'nope')).toBe('1-15');
		expect(zoneWindow({ ...data, windows: [] })).toBeNull();
	});

	it('gives one row per area with every model beside the baseline for each measure', () => {
		const table = zoneTable(skillByArea(), '1-15');

		expect(table.columns).toEqual(['AIFS', 'FuXi', 'Climatology']);
		expect(table.rows.map((row) => row.area)).toEqual(['East Gojjam', 'West Gojjam']);
		const [onset, falseAlarms, misses] = table.rows[1].measures;
		expect(onset.map((cell) => cell.text)).toEqual(['6.2 d', '4.8 d', '7.5 d']);
		expect(falseAlarms.map((cell) => cell.text)).toEqual(['30%', '25%', '40%']);
		expect(misses.map((cell) => cell.text)).toEqual(['10%', '20%', '15%']);
	});

	it('marks the best value in each row and measure, the baseline included', () => {
		const [east, west] = zoneTable(skillByArea(), '1-15').rows;

		expect(west.measures[0].map((cell) => cell.best)).toEqual([false, true, false]);
		expect(west.measures[2].map((cell) => cell.best)).toEqual([true, false, false]);
		// Climatology beats both models on onset error in East Gojjam.
		expect(east.measures[0].map((cell) => cell.best)).toEqual([false, false, true]);
	});

	it('shows a model with no value as a dash and never as the best', () => {
		const data = skillByArea();
		const rows = data.rows.map((row) =>
			row.area === 'West Gojjam' && row.model === 'fuxi' && row.window === '1-15'
				? { ...row, onset_error_days: null }
				: row
		);
		const west = zoneTable({ ...data, rows }, '1-15').rows[1];
		expect(west.measures[0].map((cell) => cell.text)).toEqual(['6.2 d', '–', '7.5 d']);
		expect(west.measures[0].map((cell) => cell.best)).toEqual([true, false, false]);
	});

	it('leaves the baseline column out of a run that has none', () => {
		const data = skillByArea();
		const rows = data.rows.map((row) => ({ ...row, climatology: null }));
		expect(zoneTable({ ...data, rows }, '1-15').columns).toEqual(['AIFS', 'FuXi']);
	});

	it('says how many grid cells a row rests on', () => {
		expect(zoneTable(skillByArea(), '1-15').rows.map((row) => row.cells)).toEqual([
			'1 cell',
			'8–12 cells'
		]);
		expect(cellsWords([12, 12])).toBe('12 cells');
		expect(cellsWords([])).toBe('no cells');
	});

	it('keeps the areas the run did not cover, and says where the areas came from', () => {
		const data = skillByArea();
		expect(zoneTable(data, '1-15').notCovered).toEqual(['Wag Hemra']);
		expect(zoneNote(data)).toContain('areas this run was scored on');
		expect(zoneNote({ ...data, areas_from: 'boundaries' })).toContain('administrative areas');
		expect(zoneTable({ ...data, rows: [skillRow({ window: '16-30' })] }, '1-15').rows).toEqual([]);
	});

	it('says under the table that the baseline can be the best', () => {
		expect(zoneNote(skillByArea())).toContain('climatology included');
	});

	it.each(['descending', 'ascending'] as const)(
		'sorts an area the model has no value for last, %s',
		(direction) => {
			const data = skillByArea();
			const rows = data.rows.map((row) =>
				row.area === 'East Gojjam' && row.model === 'aifs'
					? { ...row, onset_error_days: null }
					: row
			);
			const unscored = zoneTable({ ...data, rows }, '1-15');

			const sorted = measureRows(unscored, 'onset_error_days', { key: 0, direction });
			expect(sorted.map((row) => row.area)).toEqual(['West Gojjam', 'East Gojjam']);
		}
	);
});

describe('a model the by-zone table leaves out', () => {
	const job = (model_name: string, status: string) => ({ model_name, status });

	it.each([
		{
			case: 'is named with the reason when its run did not finish',
			jobs: [job('aifs', 'complete'), job('fuxi', 'failed')],
			shown: ['aifs'],
			note: 'FuXi is not shown: its run did not finish.'
		},
		{
			case: 'is named with the others whose runs did not finish',
			jobs: [job('aifs', 'canceled'), job('fuxi', 'failed')],
			shown: [],
			note: 'AIFS and FuXi are not shown: their runs did not finish.'
		},
		{
			case: 'is told apart from one that is still running',
			jobs: [job('fuxi', 'running')],
			shown: ['aifs'],
			note: 'FuXi is not shown yet: it is still running.'
		},
		{
			case: 'needs no note when every model has its column',
			jobs: [job('aifs', 'complete'), job('FuXi', 'complete')],
			shown: ['aifs'],
			note: null
		},
		{ case: 'needs no note for a run with no jobs', jobs: [], shown: ['aifs'], note: null }
	])('$case', ({ jobs, shown, note }) => {
		expect(missingModelsNote(jobs, shown)).toBe(note);
	});
});
