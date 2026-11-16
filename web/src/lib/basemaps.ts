import type { StyleSpecification } from 'maplibre-gl';

import worldCountriesUrl from '$lib/assets/world-countries.geojson?url';

// Shared basemap style catalog used by every map surface (metric map, forecast
// map). Kept feature-neutral so no map couples to another feature's internals.

/**
 * Drawn entirely from files the app ships, so a machine with no internet still
 * has a map to put results on: plain water, Natural Earth country outlines, and
 * nothing that needs glyphs, sprites or tiles. The colours are the lifted dark
 * palette the forecast map applies to CARTO Dark, so cells read the same here.
 */
const BUNDLED_STYLE: StyleSpecification = {
	version: 8,
	sources: {
		countries: { type: 'geojson', data: worldCountriesUrl, attribution: 'Natural Earth' }
	},
	layers: [
		{ id: 'water', type: 'background', paint: { 'background-color': '#0b0e13' } },
		{ id: 'land', type: 'fill', source: 'countries', paint: { 'fill-color': '#151b24' } },
		{
			id: 'borders',
			type: 'line',
			source: 'countries',
			paint: { 'line-color': 'rgba(255,255,255,0.22)', 'line-width': 0.6 }
		}
	]
};

export const BASEMAP_STYLES = [
	{
		id: 'carto-dark',
		label: 'CARTO Dark',
		style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
		dark: true
	},
	{
		id: 'carto-dark-no-labels',
		label: 'CARTO Dark, no labels',
		style: 'https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json',
		dark: true
	},
	{
		id: 'carto-light',
		label: 'CARTO Light',
		style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
		dark: false
	},
	{
		id: 'carto-light-no-labels',
		label: 'CARTO Light, no labels',
		style: 'https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json',
		dark: false
	},
	{
		id: 'carto-voyager',
		label: 'CARTO Voyager',
		style: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json',
		dark: false
	},
	{
		id: 'carto-voyager-no-labels',
		label: 'CARTO Voyager, no labels',
		style: 'https://basemaps.cartocdn.com/gl/voyager-nolabels-gl-style/style.json',
		dark: false
	},
	{
		id: 'openfreemap-liberty',
		label: 'OpenFreeMap Liberty',
		style: 'https://tiles.openfreemap.org/styles/liberty',
		dark: false
	},
	{
		id: 'openfreemap-bright',
		label: 'OpenFreeMap Bright',
		style: 'https://tiles.openfreemap.org/styles/bright',
		dark: false
	},
	{
		id: 'openfreemap-positron',
		label: 'OpenFreeMap Positron',
		style: 'https://tiles.openfreemap.org/styles/positron',
		dark: false
	},
	{
		id: 'openfreemap-dark',
		label: 'OpenFreeMap Dark',
		style: 'https://tiles.openfreemap.org/styles/dark',
		dark: true
	},
	{
		id: 'openfreemap-fiord',
		label: 'OpenFreeMap Fiord',
		style: 'https://tiles.openfreemap.org/styles/fiord',
		dark: true
	},
	{ id: 'bundled', label: 'No background map', style: BUNDLED_STYLE, dark: true }
] as const satisfies readonly {
	id: string;
	label: string;
	style: string | StyleSpecification;
	dark: boolean;
}[];

export type BasemapStyleId = (typeof BASEMAP_STYLES)[number]['id'];

export const DEFAULT_BASEMAP: BasemapStyleId = 'carto-dark';

/** The basemap that works with no network, and the one a failed remote style falls back to. */
export const BUNDLED_BASEMAP: BasemapStyleId = 'bundled';

export function basemapStyle(id: BasemapStyleId): string | StyleSpecification {
	return (BASEMAP_STYLES.find((s) => s.id === id) ?? BASEMAP_STYLES[0]).style;
}

// Dark styles want a lightened land + white dot outlines; light styles the
// opposite. One predicate keeps that decision in a single place.
export function isDarkBasemap(id: BasemapStyleId): boolean {
	return BASEMAP_STYLES.find((s) => s.id === id)?.dark ?? true;
}
