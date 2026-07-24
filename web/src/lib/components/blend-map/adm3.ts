import type { BlendForecastData, BlendForecastPoint } from '$lib/api';

export type ForecastFeatureProperties = {
	color: string;
	opacity: number;
	idx: number;
};

export type ForecastFeature = GeoJSON.Feature<
	GeoJSON.Point | GeoJSON.Polygon | GeoJSON.MultiPolygon,
	ForecastFeatureProperties
>;

export type ForecastFeatureCollection = GeoJSON.FeatureCollection<
	GeoJSON.Point | GeoJSON.Polygon | GeoJSON.MultiPolygon,
	ForecastFeatureProperties
>;

export type FeatureStyle = (
	point: BlendForecastPoint,
	idx: number
) => { color: string; opacity: number };

const NAME_FIELDS = [
	'adm3_name',
	'ADM3_NAME',
	'ADM3',
	'adm3',
	'shapeName',
	'shapename',
	'shape_name',
	'name',
	'NAME'
];

const LAT_LON_ID = /^-?\d+(?:\.\d+)?_-?\d+(?:\.\d+)?$/;

export function usesNamedAreas(points: BlendForecastPoint[]): boolean {
	return points.some((point) => point.id != null && !LAT_LON_ID.test(point.id));
}

export function normalizeAreaName(value: string): string {
	return value
		.normalize('NFKD')
		.replace(/[\u0300-\u036f]/g, '')
		.toLowerCase()
		.replace(/['’`]/g, '')
		.replace(/[^a-z0-9]+/g, ' ')
		.trim()
		.replace(/\s+/g, ' ');
}

export function adminFeatureName(feature: GeoJSON.Feature): string | null {
	const properties = feature.properties ?? {};
	for (const field of NAME_FIELDS) {
		const value = properties[field];
		if (typeof value === 'string' && value.trim()) return value;
	}
	return null;
}

export function buildAdm3ForecastGeoJson(
	data: BlendForecastData,
	boundaries: GeoJSON.FeatureCollection,
	stylePoint: FeatureStyle
): ForecastFeatureCollection | null {
	const pointsByName = new Map<string, { point: BlendForecastPoint; idx: number }>();
	data.points.forEach((point, idx) => {
		if (point.id) pointsByName.set(normalizeAreaName(point.id), { point, idx });
	});

	const features = boundaries.features.flatMap((feature): ForecastFeature[] => {
		if (feature.geometry?.type !== 'Polygon' && feature.geometry?.type !== 'MultiPolygon')
			return [];
		const name = adminFeatureName(feature);
		if (!name) return [];
		const match = pointsByName.get(normalizeAreaName(name));
		if (!match) return [];
		const { color, opacity } = stylePoint(match.point, match.idx);
		return [
			{
				type: 'Feature',
				geometry: feature.geometry,
				properties: { color, opacity, idx: match.idx }
			}
		];
	});

	return features.length ? { type: 'FeatureCollection', features } : null;
}
