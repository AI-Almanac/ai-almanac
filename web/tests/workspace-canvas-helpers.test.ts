import { describe, expect, it } from 'vitest';

import { backFrom, backLink, canvasQuery, parseCanvasView } from '../src/lib/workspace/canvas-view';
import type { WorkspaceArtifact } from '../src/lib/api';
import { midnightAsDate } from '../src/lib/format/dates';
import { parseFileSummary } from '../src/lib/workspace/file-summary';
import { nameEdit } from '../src/lib/workspace/rename';
import { foldSupporting } from '../src/lib/workspace/supporting';

describe('canvas view in the URL', () => {
	it('round-trips each view through the query string', () => {
		const views = [
			{ kind: 'overview' as const },
			{ kind: 'run' as const, runKey: 'run-1' },
			{ kind: 'file' as const, artifactId: 'art-1', runKey: 'run-1' },
			{ kind: 'file' as const, artifactId: 'art-1', runKey: null }
		];
		for (const view of views) {
			const query = canvasQuery(view, new URLSearchParams());
			expect(parseCanvasView(new URLSearchParams(query))).toEqual(view);
		}
	});

	it('keeps unrelated query params', () => {
		const query = canvasQuery({ kind: 'overview' }, new URLSearchParams('run=old&tab=x'));
		expect(query).toBe('?tab=x');
	});

	it('goes back to the run a file was opened from, otherwise to the overview', () => {
		expect(backFrom({ kind: 'file', artifactId: 'a', runKey: 'r' })).toEqual({
			kind: 'run',
			runKey: 'r'
		});
		expect(backFrom({ kind: 'file', artifactId: 'a', runKey: null })).toEqual({
			kind: 'overview'
		});
		expect(backFrom({ kind: 'run', runKey: 'r' })).toEqual({ kind: 'overview' });
	});

	it('offers a way back from a file only when there is a run to go back to', () => {
		const fromRun = { kind: 'file' as const, artifactId: 'a', runKey: 'r' };
		const fromAnalysis = { kind: 'file' as const, artifactId: 'a', runKey: null };

		expect(backLink(fromRun, true)).toEqual({
			label: '← Back to run',
			view: { kind: 'run', runKey: 'r' }
		});
		expect(backLink(fromAnalysis, true)).toEqual({
			label: '← Newest run',
			view: { kind: 'overview' }
		});
		// Analyses but no runs: "Newest run" would lead straight back to this file.
		expect(backLink(fromAnalysis, false)).toBeNull();
		expect(backLink({ kind: 'overview' }, true)).toBeNull();
	});
});

describe('parseFileSummary', () => {
	it('reads dimensions, variables and the time range of a dataset', () => {
		const summary = parseFileSummary({
			dims: { lat: 10, lon: 12, init_time: 26 },
			coords: {
				lat: { size: 10, min: '3.0', max: '15.0' },
				init_time: {
					size: 26,
					min: '2024-06-01T00:00:00.000000000',
					max: '2024-09-01T00:00:00.000000000'
				}
			},
			variables: {
				far: { dims: ['lat', 'lon'], dtype: 'float32', units: '1', long_name: 'False alarm rate' },
				mae: { dims: ['lat', 'lon'], dtype: 'float32', units: null, long_name: null }
			}
		});

		expect(summary.dimensions).toEqual([
			['lat', 10],
			['lon', 12],
			['init_time', 26]
		]);
		expect(summary.variables).toEqual([
			{ name: 'far', description: 'False alarm rate', units: '1' },
			{ name: 'mae', description: null, units: null }
		]);
		expect(summary.timeRange).toMatchObject({ coord: 'init_time', steps: 26 });
		expect(midnightAsDate(summary.timeRange!.start)).toBe('2024-06-01');
	});

	it('reads image size and treats unknown shapes as empty', () => {
		expect(parseFileSummary({ width: 800, height: 600, mode: 'RGB' }).imageSize).toEqual({
			width: 800,
			height: 600
		});
		expect(parseFileSummary({ dims: 'nonsense' })).toEqual({
			dimensions: [],
			variables: [],
			timeRange: null,
			imageSize: null,
			sizeBytes: null
		});
	});
});

describe('foldSupporting', () => {
	const file = (id: string, supporting: boolean) => ({ id, supporting }) as WorkspaceArtifact;

	it('lists results first and sets supporting files apart', () => {
		const { results, supporting } = foldSupporting([
			file('outline', true),
			file('map', false),
			file('code', true)
		]);
		expect(results.map((f) => f.id)).toEqual(['map']);
		expect(supporting.map((f) => f.id)).toEqual(['outline', 'code']);
	});

	it('shows supporting files as the results when there is nothing else', () => {
		const { results, supporting } = foldSupporting([file('outline', true), file('code', true)]);
		expect(results.map((f) => f.id)).toEqual(['outline', 'code']);
		expect(supporting).toEqual([]);
	});
});

describe('nameEdit', () => {
	it('asks for the default name back when the field is emptied', () => {
		expect(nameEdit('   ', 'Onset map')).toEqual({ kind: 'rename', title: null });
	});

	it('trims a new name and ignores an unchanged one', () => {
		expect(nameEdit('  Kiremt onset ', 'Onset map')).toEqual({
			kind: 'rename',
			title: 'Kiremt onset'
		});
		expect(nameEdit('Onset map ', 'Onset map')).toEqual({ kind: 'unchanged' });
	});
});

describe('a run view opened on a map window', () => {
	it('keeps the window in the URL and reads it back', () => {
		const query = canvasQuery(
			{ kind: 'run', runKey: 'run-1', window: '1-15' },
			new URLSearchParams()
		);

		expect(parseCanvasView(new URLSearchParams(query))).toEqual({
			kind: 'run',
			runKey: 'run-1',
			window: '1-15'
		});
		expect(canvasQuery({ kind: 'overview' }, new URLSearchParams(query))).toBe('');
	});
});
