import type * as maplibregl from 'maplibre-gl';
import type { JobGridResponse } from '$lib/api';
import type { BasemapStyleId } from '$lib/basemaps';
import { gridCellBounds } from './gridData';
import type { BoundaryLevel, MapViewMode } from './types';

/**
 * How a host wants the map to open. Everything is optional and read once, when the
 * map is created; whatever is left out keeps the map's own default.
 */
export type MapInitialView = {
	metric?: string;
	modelJobId?: string;
	viewMode?: MapViewMode;
	basemap?: BasemapStyleId;
	/** Boundary outlines shown from the start. */
	boundaries?: BoundaryLevel[];
	/** Fit to the cells that hold a value rather than to the whole grid. */
	fitToScoredCells?: boolean;
	/** Offer the few basemaps most people need instead of the full catalogue. */
	shortBasemapList?: boolean;
	/** Names for the boundary levels in the host's words, e.g. "Zones". */
	boundaryLabels?: Partial<Record<BoundaryLevel, string>>;
	/**
	 * Set the colour scale where most cells are, so one extreme cell does not wash
	 * the rest out; cells beyond the scale take its end colour.
	 */
	robustColourRange?: boolean;
	/** Once the reader has moved the map, data arriving later does not re-fit it. */
	holdUserView?: boolean;
};

/**
 * The extent of the cells that were scored. A run limited to a few zones writes a
 * grid spanning its whole region with values only inside them, so this is the
 * run's area of interest; null when no cell holds a value.
 */
export function scoredBounds(
	data: Pick<JobGridResponse, 'lats' | 'lons' | 'values'>
): maplibregl.LngLatBoundsLike | null {
	const lats = data.lats.filter((_, i) => data.values[i]?.some((value) => value != null));
	const lons = data.lons.filter((_, j) => data.values.some((row) => row?.[j] != null));
	if (lats.length === 0 || lons.length === 0) return null;
	const { dlat, dlon } = gridCellBounds(data as JobGridResponse);
	return [
		[Math.min(...lons) - dlon, Math.min(...lats) - dlat],
		[Math.max(...lons) + dlon, Math.max(...lats) + dlat]
	];
}
