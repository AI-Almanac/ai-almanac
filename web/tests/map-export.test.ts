import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
	CAPTURE_TIMEOUT_MS,
	captureMap,
	legendSpec,
	type CapturableMap
} from '../src/lib/components/metric-map/exportImage';
import { mapFileName, mapTitle, titledMap } from '../src/lib/components/metric-map/exportTitle';
import { forecastLegend, forecastMapTitle } from '../src/lib/components/metric-map/forecastExport';
import { ONSET_PASSED_COLOR, binsFor } from '../src/lib/onset';

describe('the words on an exported skill map', () => {
	it('says what, which model, which lead window, which area and which years', () => {
		const title = mapTitle({
			metric: 'mean_mae',
			metricLabel: 'Mean Absolute Error',
			model: 'FuXi',
			window: 'Days 1–15',
			reference: null,
			area: 'West Gojjam, East Gojjam',
			years: '2015–2022'
		});
		expect(title).toEqual({
			title: 'Onset error (mean absolute error, days) — FuXi',
			subtitle: 'Days 1–15 · West Gojjam, East Gojjam · 2015–2022'
		});
		expect(mapFileName(title)).toBe(
			'onset-error-mean-absolute-error-days-fuxi-days-1-15-west-gojjam-east-gojjam-2015-2022.png'
		);
	});

	it('says what a comparison is against, and keeps an unfamiliar measure’s own name', () => {
		const title = mapTitle({
			metric: 'rmse',
			metricLabel: 'Root mean square error',
			model: 'AIFS',
			window: 'Days 16–30',
			reference: 'Climatology',
			area: 'Ethiopia',
			years: null
		});
		expect(title.title).toBe('Root mean square error — AIFS, compared with Climatology');
		expect(title.subtitle).toBe('Days 16–30 · Ethiopia');
	});

	it('labels the colour scale in the measure’s units', () => {
		const stops = ['#fff', '#000'];
		const data = { min: 2, max: 11.5, unit: 'days' } as never;
		expect(legendSpec({ stops, data, isDelta: false }, 'mean_mae')).toEqual({
			stops,
			low: '2.0 days',
			high: '11.5 days',
			note: ''
		});
		const rate = { min: 0, max: 1, unit: 'fraction' } as never;
		expect(
			legendSpec({ stops, data: rate, isDelta: true, deltaMaxAbs: 0.25 }, 'false_alarm_rate')
		).toEqual({ stops, low: '−25%', high: '+25%', note: 'Difference: lower is better' });
	});
});

describe('the words on an exported forecast map', () => {
	it('titles it with what it shows, its issue date and its area', () => {
		const title = forecastMapTitle({
			what: 'Chance rainy season onset begins in Week 2 (Jun 9–Jun 15).',
			issued: 'Jun 2, 2026',
			area: 'West Gojjam',
			blend: null
		});

		expect(title).toEqual({
			title: 'Chance rainy season onset begins in Week 2 (Jun 9–Jun 15)',
			subtitle: 'Forecast issued Jun 2, 2026 · West Gojjam'
		});
		expect(mapFileName(title)).toMatch(/^chance-rainy-season-onset-begins-in-week-2.*\.png$/);
		expect(titledMap('Brier skill', [null, undefined])).toEqual({
			title: 'Brier skill',
			subtitle: ''
		});
	});

	it('gives a chance map a scale up to the highest chance drawn', () => {
		const legend = forecastLegend({
			colorMode: 'window',
			daily: true,
			reversed: false,
			bins: binsFor('daily'),
			scaleMax: 0.12,
			soonest: 'Jun 9'
		});

		expect(legend).toMatchObject({ low: '0%', high: '12%' });
		expect(legend.stops).toHaveLength(5);
		expect(legend.swatches).toBeUndefined();
	});

	it('names each week’s colour on a most-likely-week map, and the grey of a passed onset', () => {
		const bins = binsFor('weekly');
		const legend = forecastLegend({
			colorMode: 'expected',
			daily: false,
			reversed: false,
			bins,
			scaleMax: 1,
			soonest: 'Jun 9'
		});

		expect(legend.swatches?.map((swatch) => swatch.label)).toEqual([
			...bins.labels,
			'Onset likely began'
		]);
		expect(legend.swatches?.at(-1)?.color).toBe(ONSET_PASSED_COLOR);
	});
});

/** A map that draws, and finishes loading, only when the test says so. */
function fakeMap(loaded: boolean) {
	const listeners = new Map<string, Set<() => void>>();
	const map = {
		loaded: () => loaded,
		once: (event: string, listener: () => void) => {
			listeners.set(event, (listeners.get(event) ?? new Set()).add(listener));
			return map;
		},
		off: (event: string, listener: () => void) => {
			listeners.get(event)?.delete(listener);
			return map;
		},
		triggerRepaint: vi.fn(),
		getCanvas: () => 'the canvas' as unknown as HTMLCanvasElement
	};
	const emit = (event: string) => {
		const waiting = [...(listeners.get(event) ?? [])];
		listeners.get(event)?.clear();
		waiting.forEach((listener) => listener());
	};
	return { map: map as unknown as CapturableMap, repaint: map.triggerRepaint, emit };
}

describe('capturing the map for export', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	const read = (canvas: HTMLCanvasElement) => `copy of ${canvas}`;

	it('copies the frame it asked for when the map is already drawn', async () => {
		const { map, emit, repaint } = fakeMap(true);

		const captured = captureMap(map, CAPTURE_TIMEOUT_MS, read);
		await vi.advanceTimersByTimeAsync(0);
		expect(repaint).toHaveBeenCalledTimes(1);
		emit('render');

		await expect(captured).resolves.toBe('copy of the canvas');
	});

	it('waits for tiles still arriving after a pan or a basemap switch', async () => {
		const { map, emit, repaint } = fakeMap(false);

		const captured = captureMap(map, CAPTURE_TIMEOUT_MS, read);
		await vi.advanceTimersByTimeAsync(1000);
		expect(repaint).not.toHaveBeenCalled();

		emit('idle');
		await vi.advanceTimersByTimeAsync(0);
		emit('render');
		await expect(captured).resolves.toBe('copy of the canvas');
	});

	it('captures what there is when the tiles take too long', async () => {
		const { map, emit, repaint } = fakeMap(false);

		const captured = captureMap(map, CAPTURE_TIMEOUT_MS, read);
		await vi.advanceTimersByTimeAsync(CAPTURE_TIMEOUT_MS);
		expect(repaint).toHaveBeenCalledTimes(1);
		emit('render');

		await expect(captured).resolves.toBe('copy of the canvas');
	});

	it('gives up when the map never redraws, as in a hidden tab', async () => {
		const { map } = fakeMap(true);

		const captured = captureMap(map, CAPTURE_TIMEOUT_MS, read);
		const outcome = expect(captured).rejects.toThrow('did not redraw');
		await vi.advanceTimersByTimeAsync(CAPTURE_TIMEOUT_MS);

		await outcome;
	});
});
