/**
 * BlendForecastMap's view choices: which toggles a forecast offers, and that
 * each (model, resolution) payload is fetched once, only when first chosen.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import BlendForecastMap from '../src/lib/components/BlendForecastMap.svelte';
import type { BlendForecastData, BlendForecastView } from '../src/lib/api';

// MapLibre needs a real WebGL context, which jsdom does not provide. The map
// never reports "load" here, so no layers are drawn; only the rail is under test.
vi.mock('maplibre-gl', () => ({
	Map: class {
		addControl = vi.fn();
		on = vi.fn();
		once = vi.fn();
		remove = vi.fn();
		resize = vi.fn();
	},
	NavigationControl: class {},
	AttributionControl: class {},
	LngLatBounds: class {},
	setWorkerUrl: () => {}
}));
vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));
vi.mock('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url', () => ({ default: '' }));

const api = vi.hoisted(() => ({ getBlendForecast: vi.fn() }));
vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, getBlendForecast: api.getBlendForecast };
});

const ALL_VIEWS: BlendForecastView[] = [
	{ model: 'weekly_model', resolution: 'weekly' },
	{ model: 'daily_model', resolution: 'weekly' },
	{ model: 'daily_model', resolution: 'daily' }
];

function payload(view: BlendForecastView, availableViews: BlendForecastView[]): BlendForecastData {
	const bins = view.resolution === 'daily' ? 29 : 5;
	return {
		issue_dates: ['2025-06-01'],
		points: [{ id: '9.0_39.0', lat: 9, lon: 39, probs: [Array(bins).fill(1 / bins)] }],
		onset_threshold: 20,
		region_id: null,
		region_name: null,
		onset_definition: null,
		available_views: availableViews
	};
}

function serve(availableViews: BlendForecastView[]) {
	api.getBlendForecast.mockImplementation(async (_job: string, view: BlendForecastView) =>
		payload(view, availableViews)
	);
}

function requestedViews(): BlendForecastView[] {
	return api.getBlendForecast.mock.calls.map(([, view]) => view);
}

beforeEach(() => {
	api.getBlendForecast.mockReset();
});

describe('BlendForecastMap views', () => {
	it('offers no view choice for a forecast with only the week-level blend', async () => {
		serve([ALL_VIEWS[0]]);
		render(BlendForecastMap, { jobId: 'job-1' });

		await screen.findByText('Week 1');
		expect(screen.queryByText('By day')).toBeNull();
		expect(screen.queryByText('Day-level blend')).toBeNull();
	});

	it('loads the daily view only when chosen, and only once', async () => {
		serve(ALL_VIEWS);
		render(BlendForecastMap, { jobId: 'job-1' });
		await screen.findByText('By day');
		expect(requestedViews()).toEqual([ALL_VIEWS[0]]);

		await fireEvent.click(screen.getByText('By day'));
		// Day 1 is the day after the forecast was issued.
		await screen.findByText('Jun 2 · Day 1');
		await fireEvent.click(screen.getByText('By week'));
		await fireEvent.click(screen.getByText('By day'));

		expect(requestedViews()).toEqual([ALL_VIEWS[0], ALL_VIEWS[2]]);
		// The blend choice belongs to the weekly view; days come from one blend only.
		expect(screen.queryByText('Day-level blend')).toBeNull();
	});

	it('switches the weekly view to the day-level blend', async () => {
		serve(ALL_VIEWS);
		render(BlendForecastMap, { jobId: 'job-1' });

		await fireEvent.click(await screen.findByText('Day-level blend'));

		await waitFor(() => expect(requestedViews()).toEqual([ALL_VIEWS[0], ALL_VIEWS[1]]));
	});
});
