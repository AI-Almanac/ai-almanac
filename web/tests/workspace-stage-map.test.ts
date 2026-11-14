import { describe, expect, it } from 'vitest';

import type { Job } from '../src/lib/api';
import { COMMON_BASEMAPS, mapCredit } from '../src/lib/basemaps';
import { scoredBounds } from '../src/lib/components/metric-map/initialView';
import {
	areaWords,
	bestModelJobId,
	shouldRepickView,
	stageMapView,
	yearsWords
} from '../src/lib/components/metric-map/stage-map';
import { metricsFor } from './fixtures/workspace';

const SCORES = [
	metricsFor('job-aifs', 'aifs', { '1-15': 6.2, '16-30': 9.0 }, { '1-15': 0.1 }),
	metricsFor('job-fuxi', 'fuxi', { '1-15': 4.8, '16-30': 9.5 }, { '1-15': 0.1 })
];

function benchmarkJob(focusArea?: unknown): Job {
	return {
		id: 'job-aifs',
		status: 'complete',
		dataset_id: 'ds',
		model_name: 'aifs',
		model_display_name: 'AIFS',
		params: { start_date: '2015-06-01', end_date: '2022-09-30', focus_area: focusArea }
	} as unknown as Job;
}

describe('the best model', () => {
	it('is the one with the lowest onset error in the lead window, never the baseline', () => {
		expect(bestModelJobId(SCORES, '1-15')).toBe('job-fuxi');
		expect(bestModelJobId(SCORES, '16-30')).toBe('job-aifs');
	});

	it('is unknown when no model was scored in the window', () => {
		expect(bestModelJobId(SCORES, '31-45')).toBeNull();
		expect(bestModelJobId([], '1-15')).toBeNull();
	});
});

describe('the model the stage map opens on', () => {
	const jobs = [{ id: 'job-aifs' }, { id: 'job-fuxi' }] as Job[];
	const metrics = [
		metricsFor('job-aifs', 'aifs', { '16-30': 9.0, '1-15': 6.24 }),
		metricsFor('job-fuxi', 'fuxi', { '16-30': 9.5, '1-15': 6.21 })
	];

	it('is the model the run’s headline names', () => {
		expect(stageMapView(jobs, metrics, undefined, 'job-aifs').modelJobId).toBe('job-aifs');
	});

	it('is worked out by the headline’s rule until the headline is read', () => {
		expect(stageMapView(jobs, metrics).modelJobId).toBe('job-fuxi');
	});

	it('is the best on the lead window asked for when that is not the headline’s', () => {
		expect(stageMapView(jobs, metrics, '16-30', 'job-fuxi').modelJobId).toBe('job-aifs');
	});

	it('ignores a headline for a model the map cannot show', () => {
		expect(stageMapView(jobs, metrics, undefined, 'job-elsewhere').modelJobId).toBe('job-fuxi');
	});
});

describe('how the stage map opens', () => {
	it('shows plain onset error for the best model on a light basemap fitted to what was scored', () => {
		const view = stageMapView([benchmarkJob()], SCORES, '16-30');
		expect(view).toMatchObject({
			metric: 'mean_mae',
			modelJobId: 'job-aifs',
			viewMode: 'single',
			basemap: 'carto-light',
			fitToScoredCells: true,
			shortBasemapList: true,
			boundaries: ['adm1'],
			robustColourRange: true,
			holdUserView: true
		});
	});

	it('falls back to the first lead window, and outlines zones when the run picked zones', () => {
		const zones = benchmarkJob({ level: 'adm2', units: ['West Gojjam'] });
		const view = stageMapView([zones], SCORES, 'not-a-window');
		expect(view.modelJobId).toBe('job-fuxi');
		expect(view.boundaries).toEqual(['adm1', 'adm2']);
	});

	it('fits to the cells holding values, or nothing when none do', () => {
		const grid = {
			lats: [8, 9, 10, 11],
			lons: [36, 37, 38, 39],
			values: [
				[null, null, null, null],
				[null, 3, 4, null],
				[null, null, 5, null],
				[null, null, null, null]
			]
		};
		expect(scoredBounds(grid)).toEqual([
			[36.5, 8.5],
			[38.5, 10.5]
		]);
		expect(scoredBounds({ ...grid, values: grid.values.map((row) => row.map(() => null)) })).toBe(
			null
		);
	});

	it('offers a short list of basemaps named by how they look, crediting what it draws', () => {
		expect(COMMON_BASEMAPS.map((choice) => choice.label)).toEqual([
			'Light',
			'Light, no place names',
			'Dark'
		]);
		expect(mapCredit('carto-light', false)).toContain('OpenStreetMap');
		expect(mapCredit('carto-light', true)).toContain('geoBoundaries');
	});
});

describe('the stage map when another model finishes', () => {
	it('picks its opening view the first time', () => {
		expect(shouldRepickView(null, 'job-a', false)).toBe(true);
	});

	it('picks again for the new set of models while the reader has left the map alone', () => {
		expect(shouldRepickView('job-a', 'job-a,job-b', false)).toBe(true);
		expect(shouldRepickView('job-a', 'job-a', false)).toBe(false);
	});

	it('keeps a view the reader changed by hand', () => {
		expect(shouldRepickView('job-a', 'job-a,job-b', true)).toBe(false);
	});
});

describe('the area and years an exported map names', () => {
	it('names a few areas, counts many, and falls back to the region', () => {
		expect(areaWords(['West Gojjam', 'East Gojjam'], 'Ethiopia')).toBe('West Gojjam, East Gojjam');
		expect(areaWords(['A', 'B', 'C', 'D', 'E'], 'Ethiopia')).toBe('5 areas in Ethiopia');
		expect(areaWords(null, 'Ethiopia')).toBe('Ethiopia');
		expect(areaWords([], null)).toBe('Whole region');
	});

	it('gives the years scored', () => {
		expect(yearsWords([benchmarkJob()])).toBe('2015–2022');
		expect(yearsWords([{ params: { start_date: '2020-06-01', end_date: '2020-09-30' } }])).toBe(
			'2020'
		);
		expect(yearsWords([{ params: {} }])).toBeNull();
	});
});
