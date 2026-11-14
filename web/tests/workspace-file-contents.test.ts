import { describe, expect, it } from 'vitest';

import type { ArtifactProvenance, ProvenanceStep } from '../src/lib/api';
import { numericColumns, parseCsv, previewKind } from '../src/lib/workspace/file-preview';
import { griddedFile } from '../src/lib/workspace/gridded-file';
import { lineageRows } from '../src/lib/workspace/lineage';
import { provenanceStep } from './fixtures/workspace';

describe('what the file page shows of a file', () => {
	it.each([
		[{ kind: 'document', format: 'python' }, 'code'],
		[{ kind: 'table', format: 'csv' }, 'table'],
		[{ kind: 'figure', format: 'png' }, 'figure'],
		[{ kind: 'document', format: 'json' }, 'text'],
		[{ kind: 'dataset', format: 'netcdf' }, null]
	])('%o shows as %s', (file, kind) => {
		expect(previewKind(file)).toBe(kind);
	});
});

describe('a gridded file in words', () => {
	const years = Array.from({ length: 27 }, (_, index) => 1998 + index);
	const variables = Object.fromEntries(
		['false_alarm_rate', 'miss_rate', 'mean_mae', ...years.map((year) => `mae_${year}`)].map(
			(name) => [name, { dims: ['lat', 'lon'], units: null, long_name: null }]
		)
	);
	const metrics = (label: string) =>
		griddedFile({ label, summary: { dims: { lat: 49, lon: 61 }, variables } })!;

	it('lists what it holds under readable names, a per-year series as one entry', () => {
		const { entries } = metrics('spatial_metrics_fuxi_1-15');

		expect(entries.map((entry) => entry.label)).toEqual([
			'False alarm rate',
			'Missed onsets',
			'Onset error, average',
			'Onset error by year, 1998–2024'
		]);
		expect(entries[3].variables).toHaveLength(27);
		expect(entries[3].unit).toBe('days');
	});

	it('says which model and lead window it scores, and what the scores measure', () => {
		const { description } = metrics('spatial_metrics_fuxi_1-15');

		expect(description).toMatch(/^FuXi, forecasts 1–15 days ahead: /);
		expect(description).toContain('false alarm');
		expect(description).toContain('at each grid point');
	});

	it('calls the climatology file the baseline', () => {
		expect(metrics('spatial_metrics_climatology_16-30').description).toMatch(
			/^The climatology baseline, forecasts 16–30 days ahead/
		);
	});

	it('falls back to the file’s own description of a variable, then to its name in words', () => {
		const file = griddedFile({
			label: 'my_grid',
			summary: {
				variables: {
					tp: { long_name: 'Total precipitation', units: 'mm' },
					onset_doy: { long_name: null, units: null },
					anomaly_2024: { long_name: null, units: null }
				}
			}
		})!;

		expect(file.entries.map((entry) => [entry.label, entry.unit])).toEqual([
			['Total precipitation', 'mm'],
			['Onset doy', null],
			['Anomaly 2024', null]
		]);
		expect(file.description).toBe('Gridded data, with one value at each grid point.');
	});

	it('has nothing to say about a file with no gridded variables', () => {
		expect(griddedFile({ label: 'table', summary: { size_bytes: 120 } })).toBeNull();
	});
});

describe('reading a CSV', () => {
	it('keeps quoted commas, doubled quotes and line breaks inside a cell', () => {
		const table = parseCsv('zone,note\r\n"Borena, south","said ""early""\nthen late"\n');
		expect(table.columns).toEqual(['zone', 'note']);
		expect(table.rows).toEqual([['Borena, south', 'said "early"\nthen late']]);
	});

	it('shows the first rows and counts them all', () => {
		const table = parseCsv('n\n1\n2\n3', 2);
		expect(table.rows).toEqual([['1'], ['2']]);
		expect(table.totalRows).toBe(3);
	});

	it('right-aligns only columns that hold numbers', () => {
		const table = parseCsv('zone,mean_doy,spread\nBorena,77,14.6\nGuji,79,\n');
		expect(numericColumns(table)).toEqual([false, true, true]);
	});
});

/** A step that opened CHIRPS for Borena and made one file. */
function step(id: string, overrides: Partial<ProvenanceStep> = {}): ProvenanceStep {
	return provenanceStep({
		id,
		created_at: '2026-11-06T09:29:00Z',
		primitive: 'acquire.open_source',
		description: 'Opened CHIRPS Ethiopia for Borena, 1 Jan 1998 to 31 Dec 2024.',
		outputs: [`${id}-out`],
		turn_id: 't1',
		...overrides
	});
}

function file(id: string, format: string) {
	return {
		id,
		label: id,
		filename: `${id}.${format}`,
		kind: 'document',
		format,
		content_hash: 'c',
		provenance_step_id: 's'
	};
}

function provenance(steps: ProvenanceStep[], viewing = 'climatology'): ArtifactProvenance {
	return {
		artifact: file(viewing, 'csv'),
		steps,
		artifacts: [file('code_1', 'python'), file('climatology', 'csv')],
		producers: [],
		lineage: ''
	};
}

describe('how a file was made', () => {
	it('folds the same step repeated in a row into one line', () => {
		const rows = lineageRows(provenance([step('a'), step('b'), step('c')]));
		expect(rows).toHaveLength(1);
		expect(rows[0].steps.map((s) => s.id)).toEqual(['a', 'b', 'c']);
	});

	it('keeps steps that did different things apart', () => {
		const rows = lineageRows(
			provenance([step('a'), step('b', { description: 'Opened CHIRPS Ethiopia for Guji.' })])
		);
		expect(rows.map((row) => row.headline)).toEqual([
			'Opened CHIRPS Ethiopia for Borena, 1 Jan 1998 to 31 Dec 2024.',
			'Opened CHIRPS Ethiopia for Guji.'
		]);
	});

	it('points a code step at the code it ran, except on that code file’s own page', () => {
		const ran = step('run', {
			primitive: 'code.run_code',
			description: 'Ran code the assistant wrote.',
			outputs: ['code_1', 'climatology']
		});
		expect(lineageRows(provenance([ran]))[0].codeFileId).toBe('code_1');
		expect(lineageRows(provenance([ran], 'code_1'))[0].codeFileId).toBeNull();
	});

	it('flags a step only when its check found a problem', () => {
		const rows = lineageRows(
			provenance([
				step('a'),
				step('b', { description: 'Drew a map.', verification: 'hash_mismatch' })
			])
		);
		expect(rows.map((row) => row.problem?.label ?? null)).toEqual([
			null,
			'Changed since it was recorded'
		]);
	});
});
