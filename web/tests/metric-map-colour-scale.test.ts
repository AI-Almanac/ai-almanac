import { describe, expect, it } from 'vitest';

import type { JobGridResponse } from '../src/lib/api';
import { robustRange, scaleEnd, scoredValues } from '../src/lib/components/metric-map/colourRange';
import { legendSpec } from '../src/lib/components/metric-map/exportImage';
import { buildDeltaGeojson } from '../src/lib/components/metric-map/gridData';
import {
	buildRawLayerEntries,
	computeRobustRanges,
	computeSharedRanges
} from '../src/lib/components/metric-map/layerPipeline';
import { rawLayerKey } from '../src/lib/components/metric-map/layerKeys';

/** Onset errors of 3–15 days over a 10 × 10 grid. */
function ordinaryErrors(): number[][] {
	return Array.from({ length: 10 }, (_, i) =>
		Array.from({ length: 10 }, (_, j) => 3 + ((i * 10 + j) % 25) / 2)
	);
}

function grid(values: (number | null)[][], metric = 'mean_mae'): JobGridResponse {
	const flat = values.flat().filter((value): value is number => value != null);
	return {
		job_id: 'job-aifs',
		model: 'aifs',
		window: '1-15',
		metric,
		lats: values.map((_, i) => 8 + i),
		lons: values[0].map((_, j) => 36 + j),
		values,
		min: Math.min(...flat),
		max: Math.max(...flat),
		unit: 'days'
	} as JobGridResponse;
}

function withOutlier(): number[][] {
	const values = ordinaryErrors();
	values[4][4] = 65;
	return values;
}

describe('a colour scale one extreme cell cannot stretch', () => {
	it('ends where the bulk of the cells are and says cells lie beyond it', () => {
		const range = robustRange(withOutlier().flat());

		expect(range?.min).toBe(3);
		expect(range?.max).toBe(15);
		expect(range).toMatchObject({ clippedLow: false, clippedHigh: true });
	});

	it('keeps the whole range when nothing stands apart from the rest', () => {
		expect(robustRange(ordinaryErrors().flat())).toEqual({
			min: 3,
			max: 15,
			clippedLow: false,
			clippedHigh: false
		});
	});

	it('does not judge outliers among a handful of cells, or without any spread', () => {
		expect(robustRange([3, 4, 65])).toMatchObject({ min: 3, max: 65, clippedHigh: false });
		expect(robustRange(Array(40).fill(5))).toMatchObject({ min: 5, max: 5, clippedHigh: false });
		expect(robustRange([])).toBeNull();
	});

	it('reads only the cells that were scored', () => {
		expect(
			scoredValues([
				{
					values: [
						[1, null],
						[null, 4]
					]
				}
			])
		).toEqual([1, 4]);
	});

	it.each([
		['15.0 days', 'high', true, '≥ 15.0 days'],
		['3.0 days', 'low', true, '≤ 3.0 days'],
		['15.0 days', 'high', false, '15.0 days']
	] as const)(
		'marks the %s %s end only when values lie beyond it (%s)',
		(text, end, beyond, shown) => {
			expect(scaleEnd(text, end, beyond)).toBe(shown);
		}
	);
});

describe('the stage map with an outlier cell', () => {
	const run = { jobId: 'job-aifs', modelName: 'aifs', colorIndex: 0 };
	const windows = [{ value: '1-15', label: 'Days 1–15' }];
	const metrics = [{ value: 'mean_mae', label: 'Onset error' }];
	const data = grid(withOutlier());
	const byKey = { [rawLayerKey('job-aifs', 'aifs', 'mean_mae', '1-15')]: data };
	const results = [{ run, windowValue: '1-15', metricValue: 'mean_mae', data }];

	function colourOf(entries: ReturnType<typeof buildRawLayerEntries>['entries'], value: number) {
		const feature = entries[0].state.geojson.features.find((each) =>
			each.properties.displayVal.endsWith(value.toFixed(3))
		);
		return feature?.properties.color;
	}

	it('spreads the colours over the ordinary cells and draws the outlier in the end colour', () => {
		const robust = buildRawLayerEntries(
			results,
			computeRobustRanges([run], windows, metrics, byKey)
		).entries;

		expect(colourOf(robust, 65)).toBe(colourOf(robust, 15));
		expect(colourOf(robust, 3)).not.toBe(colourOf(robust, 9));
		expect(robust[0].state.clipped).toEqual({ low: false, high: true });
	});

	it('gives the exported legend the range that is drawn', () => {
		const [layer] = buildRawLayerEntries(
			results,
			computeRobustRanges([run], windows, metrics, byKey)
		).entries;

		expect(legendSpec(layer.state, 'mean_mae')).toMatchObject({
			low: '3.0 days',
			high: '≥ 15.0 days',
			note: 'Values beyond the scale take its end colour'
		});
	});

	it('leaves the full range, unmarked, where the robust scale was not asked for', () => {
		const [layer] = buildRawLayerEntries(
			results,
			computeSharedRanges([run], windows, metrics, byKey)
		).entries;

		expect(legendSpec(layer.state, 'mean_mae')).toMatchObject({
			low: '3.0 days',
			high: '65.0 days',
			note: ''
		});
	});

	it('holds a difference map to the same rule', () => {
		const reference = grid(ordinaryErrors().map((row) => row.map(() => 9)));

		const plain = buildDeltaGeojson(data, reference);
		const robust = buildDeltaGeojson(data, reference, true);

		expect(plain).toMatchObject({ maxAbs: 56, clipped: false });
		expect(robust).toMatchObject({ maxAbs: 6, clipped: true });
	});
});
