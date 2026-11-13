/** Latitude/longitude boxes: drawing them, outlining them and comparing them. */
import type { FeatureCollection, Position } from 'geojson';

export type Bounds = { lat_min: number; lat_max: number; lon_min: number; lon_max: number };

export type LngLat = { lng: number; lat: number };

const EDGES = ['lat_min', 'lat_max', 'lon_min', 'lon_max'] as const;

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };

/** Bounds read from untrusted JSON, or null unless every edge is a number. */
export function parseBounds(raw: unknown): Bounds | null {
	if (typeof raw !== 'object' || raw === null) return null;
	const box = raw as Record<string, unknown>;
	if (!EDGES.every((edge) => typeof box[edge] === 'number')) return null;
	const [lat_min, lat_max, lon_min, lon_max] = EDGES.map((edge) => box[edge] as number);
	return { lat_min, lat_max, lon_min, lon_max };
}

function roundToHundredth(n: number): number {
	return Math.round(n * 100) / 100;
}

/** The box with two opposite corners at `a` and `b`, to the hundredth of a degree. */
export function boxBetween(a: LngLat, b: LngLat): Bounds {
	return {
		lat_min: roundToHundredth(Math.min(a.lat, b.lat)),
		lat_max: roundToHundredth(Math.max(a.lat, b.lat)),
		lon_min: roundToHundredth(Math.min(a.lng, b.lng)),
		lon_max: roundToHundredth(Math.max(a.lng, b.lng))
	};
}

/** A box with no area: both corners on one line. */
export function isDegenerate(box: Bounds): boolean {
	return box.lat_min === box.lat_max || box.lon_min === box.lon_max;
}

/** The box as a single polygon, or an empty collection for no box. */
export function boxOutline(box: Bounds | null): FeatureCollection {
	if (!box) return EMPTY;
	const { lat_min, lat_max, lon_min, lon_max } = box;
	const ring = [
		[lon_min, lat_min],
		[lon_max, lat_min],
		[lon_max, lat_max],
		[lon_min, lat_max],
		[lon_min, lat_min]
	];
	return {
		type: 'FeatureCollection',
		features: [
			{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }
		]
	};
}

function polygonPositions(shape: FeatureCollection): Position[] {
	return shape.features.flatMap(({ geometry }) => {
		if (geometry.type === 'Polygon') return geometry.coordinates.flat();
		if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat(2);
		return [];
	});
}

/** The smallest box around a shape's polygons, or null when it has none. */
export function shapeBounds(shape: FeatureCollection): Bounds | null {
	const [first, ...rest] = polygonPositions(shape);
	if (!first) return null;
	// Reduced rather than spread: a district boundary has more points than a call takes arguments.
	return rest.reduce<Bounds>(
		(box, [lon, lat]) => ({
			lat_min: Math.min(box.lat_min, lat),
			lat_max: Math.max(box.lat_max, lat),
			lon_min: Math.min(box.lon_min, lon),
			lon_max: Math.max(box.lon_max, lon)
		}),
		{ lat_min: first[1], lat_max: first[1], lon_min: first[0], lon_max: first[0] }
	);
}

/** Whether two boxes share any point, edges included. */
export function overlaps(a: Bounds, b: Bounds): boolean {
	return (
		Math.max(a.lat_min, b.lat_min) <= Math.min(a.lat_max, b.lat_max) &&
		Math.max(a.lon_min, b.lon_min) <= Math.min(a.lon_max, b.lon_max)
	);
}
