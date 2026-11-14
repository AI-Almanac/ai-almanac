import { describe, expect, it } from 'vitest';

import type { Job, JobMetrics, MetricDefinition } from '../src/lib/api';
import {
	compareModels,
	compareWindows,
	comparisonWindows,
	headlineWindow
} from '../src/lib/metric-comparison';
import { metricMap } from '../src/lib/metric-metadata';
import { metricsFor } from './fixtures/workspace';

const stat = (mean: number, unit: string) => ({
	mean,
	min: 0,
	max: 0,
	p25: 0,
	p50: 0,
	p75: 0,
	p90: 0,
	unit
});

function metrics(jobId: string, model: string, mae: number, far: number): JobMetrics {
	const row = (window: string, who: string, m: number, f: number) => ({
		window,
		model: who,
		tolerance_days: 3,
		metrics: {
			mean_mae: stat(m, 'days'),
			false_alarm_rate: stat(f, 'fraction'),
			mae_2019: stat(m + 1, 'days')
		}
	});
	return {
		job_id: jobId,
		windows: [
			row('16-30', model, mae + 5, far),
			row('1-15', model, mae, far),
			row('1-15', 'climatology', 11.9, 0.66),
			row('16-30', 'climatology', 19.7, 0.22)
		],
		grid: null,
		bbox: null
	};
}

const jobs = [
	{ id: 'j-fuxi', model_name: 'fuxi', model_display_name: 'FuXi' },
	{ id: 'j-aifs', model_name: 'aifs', model_display_name: 'AIFS' }
] as Job[];

const definitions: MetricDefinition[] = [
	{
		id: 'mean_mae',
		label: 'Mean absolute error',
		abbreviation: 'MAE',
		unit: 'days',
		lower_is_better: true,
		description: ''
	},
	{
		id: 'false_alarm_rate',
		label: 'False alarm rate',
		abbreviation: 'FAR',
		unit: 'fraction',
		lower_is_better: true,
		description: ''
	}
];

describe('model comparison', () => {
	const all = [metrics('j-fuxi', 'FuXi', 6.6, 0.35), metrics('j-aifs', 'AIFS', 6.4, 0.41)];

	it('puts models in rows and metrics in columns, marks the best, and ends with climatology', () => {
		const table = compareModels(jobs, all, '1-15', metricMap(definitions));

		expect(table.columns.map((c) => c.label)).toEqual(['Mean absolute error', 'False alarm rate']);
		expect(table.rows.map((r) => r.model)).toEqual(['AIFS', 'FuXi', 'Climatology']);
		const [aifs, fuxi, climatology] = table.rows;
		expect(aifs.cells.map((c) => c.text)).toEqual(['6.4 d', '41.0%']);
		expect(aifs.cells.map((c) => c.best)).toEqual([true, false]);
		expect(fuxi.cells.map((c) => c.best)).toEqual([false, true]);
		expect(climatology.climatology).toBe(true);
		expect(climatology.cells.every((c) => !c.best)).toBe(true);
	});

	it('leaves the per-year error columns out', () => {
		const table = compareModels(jobs, all, '16-30', metricMap(definitions));
		expect(table.columns.some((c) => c.metric.startsWith('mae_'))).toBe(false);
		expect(table.rows[0].cells[0].text).toBe('11.4 d');
	});
});

describe('the best value in the models side by side', () => {
	const beatenByClimatology = [
		metricsFor('j-aifs', 'aifs', { '1-15': 6.4 }, { '1-15': 5 }),
		metricsFor('j-fuxi', 'fuxi', { '1-15': 6.6 }, { '1-15': 5 })
	];
	const bestRows = (options?: { baselineCompetes: boolean }) =>
		compareModels(jobs, beatenByClimatology, '1-15', metricMap(definitions.slice(0, 1)), options)
			.rows.filter((row) => row.cells[0].best)
			.map((row) => row.model);

	it('is climatology when climatology beats every model, on the workspace stage', () => {
		expect(bestRows({ baselineCompetes: true })).toEqual(['Climatology']);
	});

	it('stays the best model elsewhere', () => {
		expect(bestRows()).toEqual(['AIFS']);
	});
});

describe('lead windows in one order everywhere', () => {
	it('puts the shortest lead first and the whole season last', () => {
		expect(['all', '16-30', '31-45', '1-15'].sort(compareWindows)).toEqual([
			'1-15',
			'16-30',
			'31-45',
			'all'
		]);
		expect(comparisonWindows([metricsFor('j', 'aifs', { all: 1, '16-30': 1, '1-15': 1 })])).toEqual(
			['1-15', '16-30', 'all']
		);
	});

	it('reads the headline from days 1–15, else from the shortest lead', () => {
		expect(headlineWindow(['1-7', '1-15', '16-30'])).toBe('1-15');
		expect(headlineWindow(['all', '16-30', '8-14'])).toBe('8-14');
		expect(headlineWindow([])).toBeUndefined();
	});
});
