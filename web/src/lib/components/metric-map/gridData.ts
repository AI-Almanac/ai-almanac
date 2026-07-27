import type * as maplibregl from 'maplibre-gl';
import type { JobGridResponse } from '$lib/api';
import { DIVERGING_STOPS } from './constants';
import { isHigherBetterMetric } from './mapUi';
import type { GridFeature, GridFeatureCollection } from './types';

export function lerpHex(a: string, b: string, t: number): string {
	const parse = (h: string) => [
		parseInt(h.slice(1, 3), 16),
		parseInt(h.slice(3, 5), 16),
		parseInt(h.slice(5, 7), 16)
	];
	const [ar, ag, ab] = parse(a);
	const [br, bg, bb] = parse(b);
	const r = Math.round(ar + (br - ar) * t);
	const g = Math.round(ag + (bg - ag) * t);
	const b2 = Math.round(ab + (bb - ab) * t);
	return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b2.toString(16).padStart(2, '0')}`;
}

export function interpolateStops(stops: string[], t: number): string {
	if (stops.length === 0) return '#cccccc';
	if (t <= 0) return stops[0];
	if (t >= 1) return stops[stops.length - 1];
	const seg = t * (stops.length - 1);
	const lo = Math.floor(seg);
	const hi = Math.min(lo + 1, stops.length - 1);
	return lerpHex(stops[lo], stops[hi], seg - lo);
}

export function gridCellBounds(data: JobGridResponse) {
	const dlat = data.lats.length > 1 ? Math.abs(data.lats[1] - data.lats[0]) / 2 : 0.5;
	const dlon = data.lons.length > 1 ? Math.abs(data.lons[1] - data.lons[0]) / 2 : 0.5;
	return { dlat, dlon };
}

export function boundsFromGrid(data: JobGridResponse): maplibregl.LngLatBoundsLike | null {
	if (data.lats.length === 0 || data.lons.length === 0) return null;
	const { dlat, dlon } = gridCellBounds(data);
	const west = Math.min(...data.lons) - dlon;
	const east = Math.max(...data.lons) + dlon;
	const south = Math.min(...data.lats) - dlat;
	const north = Math.max(...data.lats) + dlat;
	return [
		[west, south],
		[east, north]
	];
}

export function buildRawGeojson(data: JobGridResponse, stops: string[]): GridFeatureCollection {
	const { lats, lons, values, min, max } = data;
	const range = max - min || 1;
	const biasMaxAbs = data.metric === 'bias' ? Math.max(Math.abs(min), Math.abs(max)) || 1 : null;
	const features: GridFeature[] = [];
	const { dlat, dlon } = gridCellBounds(data);

	for (let i = 0; i < lats.length; i++) {
		for (let j = 0; j < lons.length; j++) {
			const val = values[i]?.[j];
			if (val == null) continue;
			const lat = lats[i];
			const lon = lons[j];
			const t = biasMaxAbs == null ? (val - min) / range : (val + biasMaxAbs) / (2 * biasMaxAbs);
			const color = interpolateStops(stops, t);
			const coords = [
				[lon - dlon, lat - dlat],
				[lon + dlon, lat - dlat],
				[lon + dlon, lat + dlat],
				[lon - dlon, lat + dlat],
				[lon - dlon, lat - dlat]
			];
			features.push({
				type: 'Feature',
				properties: {
					color,
					displayVal: `${data.metric}: ${val.toFixed(3)}`,
					lat,
					lon
				},
				geometry: { type: 'Polygon', coordinates: [coords] }
			});
		}
	}
	return { type: 'FeatureCollection', features };
}

export function buildSharedRawGeojson(
	data: JobGridResponse,
	stops: string[],
	min: number,
	max: number
): GridFeatureCollection {
	return buildRawGeojson({ ...data, min, max }, stops);
}

export function buildDeltaGeojson(
	data: JobGridResponse,
	referenceData: JobGridResponse
): { geojson: GridFeatureCollection; maxAbs: number } {
	const { lats, lons, values } = data;
	const features: GridFeature[] = [];
	const { dlat, dlon } = gridCellBounds(data);
	const deltas: (number | null)[][] = lats.map((_, i) =>
		lons.map((__, j) => {
			const modelVal = values[i]?.[j];
			const referenceVal = referenceData.values[i]?.[j];
			if (modelVal == null || referenceVal == null) return null;
			return modelVal - referenceVal;
		})
	);
	let maxAbs = 0;
	for (const row of deltas) {
		for (const d of row) {
			if (d != null) maxAbs = Math.max(maxAbs, Math.abs(d));
		}
	}
	if (maxAbs === 0) maxAbs = 1;

	for (let i = 0; i < lats.length; i++) {
		for (let j = 0; j < lons.length; j++) {
			const delta = deltas[i]?.[j];
			if (delta == null) continue;
			const modelVal = values[i]?.[j] as number;
			const lat = lats[i];
			const lon = lons[j];
			const t = isHigherBetterMetric(data.metric)
				? (maxAbs - delta) / (2 * maxAbs)
				: (delta + maxAbs) / (2 * maxAbs);
			const color = interpolateStops(DIVERGING_STOPS, t);
			const coords = [
				[lon - dlon, lat - dlat],
				[lon + dlon, lat - dlat],
				[lon + dlon, lat + dlat],
				[lon - dlon, lat + dlat],
				[lon - dlon, lat - dlat]
			];
			features.push({
				type: 'Feature',
				properties: {
					color,
					displayVal: `${data.metric}: ${modelVal.toFixed(3)} (Delta: ${delta >= 0 ? '+' : ''}${delta.toFixed(3)})`,
					lat,
					lon
				},
				geometry: { type: 'Polygon', coordinates: [coords] }
			});
		}
	}
	return {
		geojson: { type: 'FeatureCollection', features },
		maxAbs
	};
}
