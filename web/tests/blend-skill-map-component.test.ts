/**
 * BlendSkillMap's map lifecycle.
 *
 * The map host sits behind an {#if} that is false while the fetch is in flight,
 * so it does not exist during onMount. Building the map there left a permanently
 * blank frame — nothing retried once the element appeared. These tests pin the
 * construction to the element appearing, which is the only thing that made the
 * failure visible.
 */
import { render, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import BlendSkillMap from '../src/routes/blends/BlendSkillMap.svelte';
import type { BlendCellMetrics } from '../src/lib/api';

// MapLibre needs a real WebGL context, which jsdom does not provide.
const maplibre = vi.hoisted(() => {
	const instances: {
		container: unknown;
		style: unknown;
		emit: (event: string) => void;
		handlers: Map<string, unknown>;
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

const api = vi.hoisted(() => ({ getBlendCellMetrics: vi.fn() }));
vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, getBlendCellMetrics: api.getBlendCellMetrics };
});

function metrics(overrides: Partial<BlendCellMetrics> = {}): BlendCellMetrics {
	return {
		job_id: 'job-1',
		baseline_model: 'unc_clim_raw',
		cell_size_deg: 0.25,
		min_observations: 10,
		grids: [
			{
				metric: 'ranked_probability_skill_score',
				label: 'Ranked Probability Skill Score',
				lats: [10, 10.25],
				lons: [33],
				values: [[0.2], [-0.1]],
				counts: [[24], [24]],
				scale_max_abs: 0.2,
				value_min: -0.1,
				value_max: 0.2,
				clipped: 0
			}
		],
		...overrides
	};
}

beforeEach(() => {
	maplibre.instances.length = 0;
	api.getBlendCellMetrics.mockReset();
});

describe('BlendSkillMap', () => {
	it('builds the map once the host appears after the fetch resolves', async () => {
		api.getBlendCellMetrics.mockResolvedValue(metrics());
		render(BlendSkillMap, { jobId: 'job-1' });

		// The host is hidden while loading, so construction cannot happen on mount.
		expect(maplibre.instances).toHaveLength(0);
		await waitFor(() => expect(maplibre.instances).toHaveLength(1));
		expect(maplibre.instances[0].container).toBeInstanceOf(HTMLElement);
	});

	it('adds the cell layers once the style has parsed', async () => {
		api.getBlendCellMetrics.mockResolvedValue(metrics());
		render(BlendSkillMap, { jobId: 'job-1' });
		await waitFor(() => expect(maplibre.instances).toHaveLength(1));

		const instance = maplibre.instances[0];
		// Layers cannot be added before the style exists; maplibre throws if they are.
		expect(instance.addSource).not.toHaveBeenCalled();
		instance.emit('style.load');
		await waitFor(() => expect(instance.addSource).toHaveBeenCalledTimes(1));
		// A fill and an outline, added exactly once between them.
		expect(instance.addLayer).toHaveBeenCalledTimes(2);
		// And the camera is framed on the data rather than left at the world view.
		expect(instance.fitBounds).toHaveBeenCalledTimes(1);
	});

	it('does not wait on the basemap finishing its first render', async () => {
		// 'load' additionally waits for sprites and glyphs from a third-party CDN.
		// Gating our own cells on it means one stalled request blanks the map.
		api.getBlendCellMetrics.mockResolvedValue(metrics());
		render(BlendSkillMap, { jobId: 'job-1' });
		await waitFor(() => expect(maplibre.instances).toHaveLength(1));

		const instance = maplibre.instances[0];
		instance.emit('style.load');
		await waitFor(() => expect(instance.addSource).toHaveBeenCalled());
		// Never needed 'load' at all.
		expect(instance.handlers.has('load')).toBe(false);
	});

	it('never builds a map when the blend has no per-point grids', async () => {
		api.getBlendCellMetrics.mockResolvedValue(metrics({ grids: [] }));
		const { findByText } = render(BlendSkillMap, { jobId: 'job-1' });
		await findByText(/no per-grid-point summary/i);
		expect(maplibre.instances).toHaveLength(0);
	});

	it('surfaces a failed fetch instead of a blank frame', async () => {
		api.getBlendCellMetrics.mockRejectedValue(new Error('outputs unreachable'));
		const { findByText } = render(BlendSkillMap, { jobId: 'job-1' });
		await findByText('outputs unreachable');
		expect(maplibre.instances).toHaveLength(0);
	});

	it('refetches when the selected blend changes', async () => {
		api.getBlendCellMetrics.mockResolvedValue(metrics());
		const { rerender } = render(BlendSkillMap, { jobId: 'job-1' });
		await waitFor(() => expect(api.getBlendCellMetrics).toHaveBeenCalledWith('job-1'));

		api.getBlendCellMetrics.mockResolvedValue(metrics({ job_id: 'job-2' }));
		await rerender({ jobId: 'job-2' });
		await waitFor(() => expect(api.getBlendCellMetrics).toHaveBeenCalledWith('job-2'));
	});
});
