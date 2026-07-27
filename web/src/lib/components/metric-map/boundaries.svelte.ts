import type * as maplibregl from 'maplibre-gl';
import { getRegionBoundary } from '$lib/api';
import { BOUNDARY_LEVELS } from './constants';
import { boundaryLayerId, boundarySourceId } from './layerKeys';
import type {
	BoundaryCacheEntry,
	BoundaryLayerState,
	BoundaryLevel,
	BoundaryStyleDef
} from './types';

type BoundaryMetadata = Awaited<ReturnType<typeof getRegionBoundary>>['metadata'];

function cacheKey(level: BoundaryLevel, region: string) {
	return `${region.trim().toLowerCase()}||${level}`;
}

function cacheEntry(
	metadata: BoundaryMetadata,
	geojson: unknown,
	level: BoundaryLevel
): BoundaryCacheEntry {
	return {
		label: `${metadata.boundaryName ?? 'Region'} ${metadata.boundaryType ?? BOUNDARY_LEVELS[level].type}`,
		source: metadata.boundarySource ?? 'geoBoundaries',
		geojson
	};
}

/**
 * Owns the administrative boundary overlays: visibility per level, load and
 * error state, the per-region GeoJSON cache, and the MapLibre line layers.
 */
export class BoundaryLayers {
	layers = $state<Record<BoundaryLevel, BoundaryLayerState | null>>({
		adm1: null,
		adm2: null
	});
	loading = $state<Set<BoundaryLevel>>(new Set());
	errors = $state<Partial<Record<BoundaryLevel, string>>>({});
	visibleLevels = $state<Set<BoundaryLevel>>(new Set());

	readonly visibleLayers = $derived(
		[...this.visibleLevels].map((level) => this.layers[level]).filter((layer) => layer != null)
	);

	private cache = new Map<string, BoundaryCacheEntry>();

	constructor(
		private getMap: () => maplibregl.Map | null,
		private getRegion: () => string | undefined,
		// Per-map line styling; defaults to the shared BOUNDARY_LEVELS so callers
		// that don't care (the benchmark map) are unaffected.
		private styles: Record<BoundaryLevel, BoundaryStyleDef> = BOUNDARY_LEVELS
	) {}

	/** Remove all boundary layers from the map and reset load state. */
	clearFromMap() {
		const map = this.getMap();
		for (const state of Object.values(this.layers)) {
			if (!state || !map) continue;
			if (map.getLayer(`${state.layerId}-halo`)) map.removeLayer(`${state.layerId}-halo`);
			if (map.getLayer(state.layerId)) map.removeLayer(state.layerId);
			if (map.getSource(state.sourceId)) map.removeSource(state.sourceId);
		}
		this.layers = { adm1: null, adm2: null };
		this.errors = {};
		this.loading = new Set();
	}

	/** Re-add the layers for every visible level (after the grid layers reload). */
	reloadVisible() {
		for (const level of this.visibleLevels) void this.loadLevel(level);
	}

	toggle(level: BoundaryLevel) {
		const next = new Set(this.visibleLevels);
		if (next.has(level)) {
			next.delete(level);
			this.setVisibility(level, false);
		} else {
			next.add(level);
			this.setVisibility(level, true);
			void this.loadLevel(level);
		}
		this.visibleLevels = next;
	}

	private setVisibility(level: BoundaryLevel, visible: boolean) {
		const map = this.getMap();
		if (!map) return;
		for (const id of [`${boundaryLayerId(level)}-halo`, boundaryLayerId(level)]) {
			if (map.getLayer(id)) {
				map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
			}
		}
	}

	private async loadLevel(level: BoundaryLevel) {
		const map = this.getMap();
		if (!map || this.layers[level] || this.loading.has(level)) return;
		const region = this.getRegion();
		if (!region) {
			this.errors = { ...this.errors, [level]: 'No geoBoundaries mapping for this region' };
			return;
		}
		const cached = this.cache.get(cacheKey(level, region));
		if (cached) {
			const state = this.addLayerState(level, cached);
			if (state) this.layers = { ...this.layers, [level]: state };
			return;
		}

		this.loading = new Set([...this.loading, level]);
		this.errors = { ...this.errors, [level]: '' };
		try {
			const { metadata, geojson } = await getRegionBoundary(region, level);
			const entry = cacheEntry(metadata, geojson, level);
			this.cache.set(cacheKey(level, region), entry);
			const state = this.addLayerState(level, entry);
			if (state) this.layers = { ...this.layers, [level]: state };
		} catch (e) {
			this.errors = {
				...this.errors,
				[level]: e instanceof Error ? e.message : 'Failed to load boundaries'
			};
			const next = new Set(this.visibleLevels);
			next.delete(level);
			this.visibleLevels = next;
		} finally {
			const next = new Set(this.loading);
			next.delete(level);
			this.loading = next;
		}
	}

	private addLayerState(
		level: BoundaryLevel,
		entry: BoundaryCacheEntry
	): BoundaryLayerState | null {
		const map = this.getMap();
		if (!map) return null;
		const style = this.styles[level];
		const sourceId = boundarySourceId(level);
		const haloLayerId = `${boundaryLayerId(level)}-halo`;
		const layerId = boundaryLayerId(level);
		if (map.getLayer(haloLayerId)) map.removeLayer(haloLayerId);
		if (map.getLayer(layerId)) map.removeLayer(layerId);
		if (map.getSource(sourceId)) map.removeSource(sourceId);
		map.addSource(sourceId, {
			type: 'geojson',
			data: entry.geojson as GeoJSON.GeoJSON
		});
		const visibility = this.visibleLevels.has(level) ? 'visible' : 'none';
		map.addLayer({
			id: haloLayerId,
			type: 'line',
			source: sourceId,
			layout: { visibility },
			paint: {
				'line-color': style.haloColor,
				'line-width': style.haloWidth
			}
		});
		map.addLayer({
			id: layerId,
			type: 'line',
			source: sourceId,
			layout: { visibility },
			paint: {
				'line-color': style.strokeColor,
				'line-width': style.strokeWidth
			}
		});
		return {
			layerId,
			sourceId,
			label: entry.label,
			source: entry.source,
			geojson: entry.geojson
		};
	}
}
