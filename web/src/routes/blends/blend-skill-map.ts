/**
 * Turning a per-grid-point skill grid into map polygons.
 *
 * Kept out of the Svelte component so it can be unit tested — maplibre needs a
 * real WebGL context and doesn't run under jsdom.
 */
import type { BlendCellGrid } from '$lib/api';
import { interpolateStops } from '$lib/components/metric-map/gridData';

/**
 * ColorBrewer BrBG: brown for worse than climatology, teal for better.
 *
 * Not the green/rust of the skill table above it. On the table a tint sits behind
 * a number the reader can always fall back on, so the hue is decoration; on a map
 * colour is the only channel, which rules out a red/green pair. BrBG keeps the
 * table's "warm is worse, cool-green is better" reading while staying legible to
 * red-green colourblind readers.
 */
export const SKILL_STOPS = ['#8c510a', '#d8b365', '#f5f5f5', '#5ab4ac', '#01665e'];

export type SkillCellFeature = GeoJSON.Feature<
	GeoJSON.Polygon,
	{
		color: string;
		opacity: number;
		lat: number;
		lon: number;
		skill: number;
		observations: number | null;
		/** True when |skill| exceeded the ramp and the colour is saturated. */
		clipped: boolean;
	}
>;

export type SkillCellCollection = GeoJSON.FeatureCollection<
	GeoJSON.Polygon,
	SkillCellFeature['properties']
>;

/**
 * Position on the diverging ramp, with zero fixed at the midpoint.
 *
 * Fixing the midpoint is the point of the whole scale: it makes "beats
 * climatology" a colour family rather than a value the reader has to look up.
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
					clipped: extent > 0 && Math.abs(skill) > extent
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
export function shareBeatingBaseline(grid: BlendCellGrid): { better: number; total: number } {
	let better = 0;
	let total = 0;
	for (const row of grid.values) {
		for (const value of row) {
			if (value == null) continue;
			total += 1;
			if (value > 0) better += 1;
		}
	}
	return { better, total };
}
