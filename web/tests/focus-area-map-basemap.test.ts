/**
 * FocusAreaMap has no basemap picker, so an unreachable online basemap must
 * quietly give way to the bundled one, leaving the region and areas pickable.
 */
import { render, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FocusAreaMap from '../src/lib/components/FocusAreaMap.svelte';
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
	AttributionControl: class {},
	LngLatBounds: class {},
	setWorkerUrl: () => {}
}));
vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));
vi.mock('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url', () => ({ default: '' }));

beforeEach(() => {
	maplibre.instances.length = 0;
});

describe('FocusAreaMap basemap', () => {
	it('switches to the bundled basemap when the online one cannot be fetched', async () => {
		render(FocusAreaMap, { value: null, onchange: () => {} });
		await waitFor(() => expect(maplibre.instances).toHaveLength(1));
		const [instance] = maplibre.instances;
		expect(instance.style).toBe(basemapStyle(DEFAULT_BASEMAP));

		instance.emit('error', {
			error: Object.assign(new Error('Failed to fetch'), { url: basemapStyle(DEFAULT_BASEMAP) })
		});
		expect(instance.setStyle).toHaveBeenCalledExactlyOnceWith(basemapStyle(BUNDLED_BASEMAP), {
			diff: false
		});
	});
});
