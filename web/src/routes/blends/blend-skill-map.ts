/**
 * Turning a per-grid-point skill grid into map polygons.
 *
 * Kept out of the Svelte component so it can be unit tested — maplibre needs a
 * real WebGL context and doesn't run under jsdom.
 */
import type { BlendAreaMetric, BlendCellGrid, SkillLayer } from '$lib/api';
import { adminFeatureName, normalizeAreaName } from '$lib/components/blend-map/adm3';
import { DIVERGING_STOPS } from '$lib/components/metric-map/constants';
import { interpolateStops } from '$lib/components/metric-map/gridData';

/**
 * The benchmark map's diverging scale, reversed.
 *
 * Both maps answer "better or worse than climatology at this point", so they
 * share a palette — blue is better and red is worse on either page. The reversal
 * is what keeps that true: the benchmark map plots deltas of error metrics, where
 * better is negative, while skill is better when positive. Same color meanings,
 * opposite axis direction.
 */
export const SKILL_STOPS = [...DIVERGING_STOPS].reverse();

export type SkillCellFeature = GeoJSON.Feature<
	GeoJSON.Polygon | GeoJSON.MultiPolygon | GeoJSON.Point,
	{
		color: string;
		opacity: number;
		lat: number;
		lon: number;
		skill: number;
		observations: number | null;
		/** True when |skill| exceeded the ramp and the color is saturated. */
		clipped: boolean;
		/** The administrative unit's name; null for grid cells. */
		name: string | null;
	}
>;

export type SkillCellCollection = GeoJSON.FeatureCollection<
	GeoJSON.Polygon | GeoJSON.MultiPolygon | GeoJSON.Point,
	SkillCellFeature['properties']
>;

/**
 * Position on the diverging ramp, with zero fixed at the midpoint.
 *
 * Fixing the midpoint is the point of the whole scale: it makes "beats
 * climatology" a color family rather than a value the reader has to look up.
 */
export function rampPosition(skill: number, extent: number): number {
	if (!(extent > 0)) return 0.5;
	const clamped = Math.max(-extent, Math.min(extent, skill));
	return (clamped + extent) / (2 * extent);
}

/**
 * Points scored on few observations are muted rather than hidden.
 *
 * Dropping them would misrepresent coverage, and drawing them at full strength
 * would let a point resting on a dozen years read as firmly as one resting on
 * fifty. Below the floor opacity falls off with the count.
 */
export function cellOpacity(observations: number | null, floor: number): number {
	if (observations == null) return 0.35;
	if (floor <= 0 || observations >= floor) return 0.85;
	return 0.35 + 0.5 * (observations / floor);
}

/** Half-widths of a grid cell, from the spacing between adjacent coordinates. */
export function halfCell(
	grid: BlendCellGrid,
	fallbackDeg: number | null
): {
	dlat: number;
	dlon: number;
} {
	const spacing = (values: number[]) => {
		if (values.length > 1) {
			let smallest = Infinity;
			for (let i = 1; i < values.length; i++) {
				const gap = values[i] - values[i - 1];
				if (gap > 0) smallest = Math.min(smallest, gap);
			}
			if (Number.isFinite(smallest)) return smallest;
		}
		// A region one row or column wide has no spacing of its own to measure.
		return fallbackDeg && fallbackDeg > 0 ? fallbackDeg : 0.25;
	};
	return { dlat: spacing(grid.lats) / 2, dlon: spacing(grid.lons) / 2 };
}

export function buildSkillCells(
	grid: BlendCellGrid,
	options: { minObservations: number; cellSizeDeg: number | null }
): SkillCellCollection {
	const extent = grid.scale_max_abs ?? 0;
	const { dlat, dlon } = halfCell(grid, options.cellSizeDeg);
	const features: SkillCellFeature[] = [];

	for (let i = 0; i < grid.lats.length; i++) {
		for (let j = 0; j < grid.lons.length; j++) {
			const skill = grid.values[i]?.[j];
			if (skill == null) continue;
			const lat = grid.lats[i];
			const lon = grid.lons[j];
			const observations = grid.counts[i]?.[j] ?? null;
			features.push({
				type: 'Feature',
				properties: {
					color: interpolateStops(SKILL_STOPS, rampPosition(skill, extent)),
					opacity: cellOpacity(observations, options.minObservations),
					lat,
					lon,
					skill,
					observations,
					clipped: extent > 0 && Math.abs(skill) > extent,
					name: null
				},
				geometry: {
					type: 'Polygon',
					coordinates: [
						[
							[lon - dlon, lat - dlat],
							[lon + dlon, lat - dlat],
							[lon + dlon, lat + dlat],
							[lon - dlon, lat + dlat],
							[lon - dlon, lat - dlat]
						]
					]
				}
			});
		}
	}
	return { type: 'FeatureCollection', features };
}

/** Bounding box of the drawn cells, for framing the map. */
export function skillBounds(
	grid: BlendCellGrid,
	cellSizeDeg: number | null
): [[number, number], [number, number]] | null {
	if (grid.lats.length === 0 || grid.lons.length === 0) return null;
	const { dlat, dlon } = halfCell(grid, cellSizeDeg);
	return [
		[Math.min(...grid.lons) - dlon, Math.min(...grid.lats) - dlat],
		[Math.max(...grid.lons) + dlon, Math.max(...grid.lats) + dlat]
	];
}

/** Share of points that beat climatology — the map's one-line summary. */
export function shareBeatingBaseline(layer: SkillLayer): { better: number; total: number } {
	const values = scoredValues(layer);
	return { better: values.filter((value) => value > 0).length, total: values.length };
}

export function isAreaMetric(layer: SkillLayer): layer is BlendAreaMetric {
	return 'areas' in layer;
}

/** Every non-missing skill value, whichever shape the metric came in. */
export function scoredValues(layer: SkillLayer): number[] {
	if (isAreaMetric(layer)) {
		return layer.areas.flatMap((area) => (area.skill == null ? [] : [area.skill]));
	}
	return layer.values.flatMap((row) => row.filter((value): value is number => value != null));
}

/** Scored points whose observation count sits under the floor, so the caption can name them. */
export function lowCountPoints(layer: SkillLayer, floor: number): number {
	if (isAreaMetric(layer)) {
		return layer.areas.filter((a) => a.skill != null && a.count != null && a.count < floor).length;
	}
	let count = 0;
	layer.counts.forEach((row, i) =>
		row.forEach((n, j) => {
			if (layer.values[i]?.[j] != null && n != null && n < floor) count += 1;
		})
	);
	return count;
}

/**
 * Paint each named area with its skill. Areas whose name matches a boundary
 * feature take that polygon; the rest become a point at their centroid, drawn as
 * a marker so a unit the boundary file spells differently is still on the map
 * without masquerading as a grid cell.
 */
export function buildAreaSkillCells(
	metric: BlendAreaMetric,
	boundaries: GeoJSON.FeatureCollection | null,
	options: { minObservations: number }
): SkillCellCollection {
	const extent = metric.scale_max_abs ?? 0;
	const outlines = new Map<string, GeoJSON.Polygon | GeoJSON.MultiPolygon>();
	for (const feature of boundaries?.features ?? []) {
		const name = adminFeatureName(feature);
		const geometry = feature.geometry;
		if (name && (geometry?.type === 'Polygon' || geometry?.type === 'MultiPolygon')) {
			outlines.set(normalizeAreaName(name), geometry);
		}
	}
	const features: SkillCellFeature[] = [];
	for (const area of metric.areas) {
		if (area.skill == null) continue;
		features.push({
			type: 'Feature',
			properties: {
				color: interpolateStops(SKILL_STOPS, rampPosition(area.skill, extent)),
				opacity: cellOpacity(area.count, options.minObservations),
				lat: area.lat,
				lon: area.lon,
				skill: area.skill,
				observations: area.count,
				clipped: extent > 0 && Math.abs(area.skill) > extent,
				name: area.id
			},
			geometry: outlines.get(normalizeAreaName(area.id)) ?? {
				type: 'Point',
				coordinates: [area.lon, area.lat]
			}
		});
	}
	return { type: 'FeatureCollection', features };
}

/** Bounding box of every drawn feature, for framing the camera. */
export function featureBounds(
	collection: SkillCellCollection
): [[number, number], [number, number]] | null {
	let west = Infinity;
	let south = Infinity;
	let east = -Infinity;
	let north = -Infinity;
	const visit = (position: GeoJSON.Position) => {
		west = Math.min(west, position[0]);
		east = Math.max(east, position[0]);
		south = Math.min(south, position[1]);
		north = Math.max(north, position[1]);
	};
	for (const { geometry } of collection.features) {
		if (geometry.type === 'Point') visit(geometry.coordinates);
		else if (geometry.type === 'Polygon')
			geometry.coordinates.forEach((ring) => ring.forEach(visit));
		else geometry.coordinates.flat().forEach((ring) => ring.forEach(visit));
	}
	return Number.isFinite(west)
		? [
				[west, south],
				[east, north]
			]
		: null;
}

/** Areas drawn at their centroid because no boundary outline matched their name. */
export function centredAreaCount(collection: SkillCellCollection): number {
	return collection.features.filter((feature) => feature.geometry.type === 'Point').length;
}
