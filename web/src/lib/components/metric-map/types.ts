import type maplibregl from 'maplibre-gl';
import type { JobGridResponse } from '$lib/api';

export type MetricDef = { value: string; label: string };
export type WindowDef = { value: string; label: string };
export type MetricWindowAvailability = Record<string, string[]>;
export type MetricWindowAvailabilityByJob = Record<string, MetricWindowAvailability>;

export type RunDef = {
	jobId: string;
	modelName: string;
	colorIndex: number;
};

export type MapViewMode = 'single' | 'baseline' | 'difference' | 'swipe';

export type GridFeature = {
	type: 'Feature';
	properties: {
		color: string;
		lat: number;
		lon: number;
		displayVal: string;
	};
	geometry: {
		type: 'Polygon';
		coordinates: number[][][];
	};
};

export type GridFeatureCollection = {
	type: 'FeatureCollection';
	features: GridFeature[];
};

export type LayerState = {
	layerId: string;
	sourceId: string;
	data: JobGridResponse;
	geojson: GridFeatureCollection;
	bounds: maplibregl.LngLatBoundsLike | null;
	stops: string[];
	isDelta: boolean;
	deltaMaxAbs?: number;
	referenceData?: JobGridResponse;
	referenceModelName?: string;
};

export type BoundaryLevel = 'adm1' | 'adm2';

export type BoundaryLayerState = {
	layerId: string;
	sourceId: string;
	label: string;
	source: string;
	geojson: unknown;
};

export type BoundaryCacheEntry = {
	label: string;
	source: string;
	geojson: unknown;
};

export type BoundaryStyleDef = {
	label: string;
	type: string;
	strokeColor: string;
	haloColor: string;
	strokeWidth: number;
	haloWidth: number;
	zIndex: number;
};
