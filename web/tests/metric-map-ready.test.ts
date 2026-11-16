/**
 * MetricMap's readiness. Its data layers need only the basemap style, so it must
 * load them once the style is in, without waiting for maplibre's `load`: that event
 * also waits for every tile and a settled frame, and in a workspace run view it never
 * fired, which left the skill maps without data.
 */
import { render, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

// The map's controls read the viewport size, which jsdom cannot answer.
vi.hoisted(() => {
	window.matchMedia = ((query: string) => ({
		matches: false,
		media: query,
		addEventListener: () => {},
		removeEventListener: () => {}
	})) as unknown as typeof window.matchMedia;
});

import MetricMap from '../src/lib/components/MetricMap.svelte';
import type { Job } from '../src/lib/api';

// MapLibre needs a real WebGL context, which jsdom does not provide.
const maplibre = vi.hoisted(() => {
	const instances: {
		container: unknown;
		style: unknown;
		emit: (event: string) => void;
		sources: Map<string, { setData: ReturnType<typeof vi.fn> }>;
		addSource: ReturnType<typeof vi.fn>;
		addLayer: ReturnType<typeof vi.fn>;
		fitBounds: ReturnType<typeof vi.fn>;
		remove: ReturnType<typeof vi.fn>;
	}[] = [];
	class FakeMap {
		handlers = new Map<string, ((...args: unknown[]) => void)[]>();
		// Sources and layers are remembered so getSource behaves like maplibre's:
		// a stub that always reports "absent" would let a redundant second render
		// re-add layers and hide that from the assertions.
		sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
		addSource = vi.fn((id: string) => {
			this.sources.set(id, { setData: vi.fn() });
		});
		addLayer = vi.fn();
		fitBounds = vi.fn();
		remove = vi.fn();
		addControl = vi.fn();
		resize = vi.fn();
		setStyle = vi.fn();
		getSource = vi.fn((id: string) => this.sources.get(id));
		getLayer = vi.fn(() => undefined);
		container: unknown;
		style: unknown;
		constructor(options: { container: unknown; style: unknown }) {
			this.container = options.container;
			this.style = options.style;
			instances.push(this as never);
		}
		on(event: string, second: unknown, third?: unknown) {
			const handler = (typeof second === 'function' ? second : third) as (
				...args: unknown[]
			) => void;
			const list = this.handlers.get(event) ?? [];
			list.push(handler);
			this.handlers.set(event, list);
			return this;
		}
		once(event: string, handler: (...args: unknown[]) => void) {
			return this.on(event, handler);
		}
		/** Fire whatever maplibre would have fired once the style finished loading. */
		emit(event: string) {
			for (const handler of this.handlers.get(event) ?? []) handler();
		}
	}
	return { instances, FakeMap };
});

vi.mock('maplibre-gl', () => ({
	Map: maplibre.FakeMap,
	NavigationControl: class {},
	AttributionControl: class {},
	setWorkerUrl: () => {}
}));
vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));
vi.mock('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url', () => ({ default: '' }));

const grids = vi.hoisted(() => ({
	getCachedJobGrid: vi.fn(async () => ({
		job_id: 'job-1',
		model: 'aifs',
		window: '1-15',
		metric: 'false_alarm_rate',
		lats: [9],
		lons: [38],
		values: [[0.4]],
		unit: 'fraction',
		min: 0,
		max: 1
	}))
}));
vi.mock('$lib/benchmarks.svelte', () => grids);

const job = { id: 'job-1', model_name: 'aifs', model_display_name: 'AIFS' } as unknown as Job;

describe('MetricMap readiness', () => {
	it('loads the skill data once the basemap style is in, without waiting for load', async () => {
		render(MetricMap, {
			jobs: [job],
			forecastWindow: '1-15',
			forecastWindows: [{ value: '1-15', label: 'Days 1–15' }],
			metrics: [{ value: 'false_alarm_rate', label: 'False alarm rate' }]
		});
		const [map] = maplibre.instances;

		map.emit('style.load');

		await waitFor(() => expect(grids.getCachedJobGrid).toHaveBeenCalled());
		expect(
			(map as unknown as { setStyle: ReturnType<typeof vi.fn> }).setStyle
		).not.toHaveBeenCalled();
	});
});
