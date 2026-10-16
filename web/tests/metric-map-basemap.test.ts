/**
 * MetricMap's basemap lifecycle: an unreachable online basemap gives way to the
 * bundled one, and the map is styled once, not again when it becomes ready.
 */
import { render, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MetricMap from '../src/lib/components/MetricMap.svelte';
import { BUNDLED_BASEMAP, DEFAULT_BASEMAP, basemapStyle } from '../src/lib/basemaps';

// MapLibre needs a real WebGL context, which jsdom does not provide.
const maplibre = vi.hoisted(() => {
	type Handler = (payload?: object) => void;
	const instances: FakeMap[] = [];
	class FakeMap {
		handlers = new Map<string, Handler[]>();
		style: unknown;
		setStyle = vi.fn();
		addControl = vi.fn();
		remove = vi.fn();
		resize = vi.fn();
		getLayer = vi.fn(() => undefined);
		getSource = vi.fn(() => undefined);
		constructor(options: { style: unknown }) {
			this.style = options.style;
			instances.push(this);
		}
		on(event: string, second: unknown, third?: unknown) {
			const handler = (typeof second === 'function' ? second : third) as Handler;
			this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler]);
			return this;
		}
		once(event: string, handler: Handler) {
			return this.on(event, handler);
		}
		off(event: string, handler: Handler) {
			this.handlers.set(
				event,
				(this.handlers.get(event) ?? []).filter((h) => h !== handler)
			);
			return this;
		}
		emit(event: string, payload: object = {}) {
			for (const handler of [...(this.handlers.get(event) ?? [])]) handler(payload);
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
// The cell inspector charts with uPlot, which reads matchMedia at import; no chart opens here.
vi.mock('uplot', () => ({ default: class {} }));

function renderMap() {
	return render(MetricMap, { jobs: [], forecastWindow: '1-15', metrics: [] });
}

beforeEach(() => {
	maplibre.instances.length = 0;
});

describe('MetricMap basemap', () => {
	it('builds on the default basemap and does not restyle once the map is ready', async () => {
		renderMap();
		await waitFor(() => expect(maplibre.instances).toHaveLength(1));
		const [instance] = maplibre.instances;
		expect(instance.style).toBe(basemapStyle(DEFAULT_BASEMAP));

		instance.emit('style.load');
		instance.emit('load');
		await Promise.resolve();
		expect(instance.setStyle).not.toHaveBeenCalled();
	});

	it('switches to the bundled basemap when the online one cannot be fetched', async () => {
		const { findByRole } = renderMap();
		await waitFor(() => expect(maplibre.instances).toHaveLength(1));
		const [instance] = maplibre.instances;

		instance.emit('error', {
			error: Object.assign(new Error('Failed to fetch'), { url: basemapStyle(DEFAULT_BASEMAP) })
		});
		expect(instance.setStyle).toHaveBeenCalledExactlyOnceWith(basemapStyle(BUNDLED_BASEMAP), {
			diff: false
		});

		instance.emit('style.load');
		instance.emit('load');
		expect(await findByRole('combobox', { name: /basemap/i })).toHaveProperty(
			'value',
			BUNDLED_BASEMAP
		);
		expect(instance.setStyle).toHaveBeenCalledOnce();
	});
});
