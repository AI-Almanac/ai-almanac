// Shared basemap style catalog used by every map surface (metric map, forecast
// map). Kept feature-neutral so no map couples to another feature's internals.
export const BASEMAP_STYLES = [
	{
		id: 'carto-dark',
		label: 'CARTO Dark',
		url: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
	},
	{
		id: 'carto-dark-no-labels',
		label: 'CARTO Dark, no labels',
		url: 'https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json'
	},
	{
		id: 'carto-light',
		label: 'CARTO Light',
		url: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'
	},
	{
		id: 'carto-light-no-labels',
		label: 'CARTO Light, no labels',
		url: 'https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json'
	},
	{
		id: 'carto-voyager',
		label: 'CARTO Voyager',
		url: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json'
	},
	{
		id: 'carto-voyager-no-labels',
		label: 'CARTO Voyager, no labels',
		url: 'https://basemaps.cartocdn.com/gl/voyager-nolabels-gl-style/style.json'
	},
	{
		id: 'openfreemap-liberty',
		label: 'OpenFreeMap Liberty',
		url: 'https://tiles.openfreemap.org/styles/liberty'
	},
	{
		id: 'openfreemap-bright',
		label: 'OpenFreeMap Bright',
		url: 'https://tiles.openfreemap.org/styles/bright'
	},
	{
		id: 'openfreemap-positron',
		label: 'OpenFreeMap Positron',
		url: 'https://tiles.openfreemap.org/styles/positron'
	},
	{
		id: 'openfreemap-dark',
		label: 'OpenFreeMap Dark',
		url: 'https://tiles.openfreemap.org/styles/dark'
	},
	{
		id: 'openfreemap-fiord',
		label: 'OpenFreeMap Fiord',
		url: 'https://tiles.openfreemap.org/styles/fiord'
	}
] as const;

export type BasemapStyleId = (typeof BASEMAP_STYLES)[number]['id'];

// Dark styles want a lightened land + white dot outlines; light styles the
// opposite. One predicate keeps that decision in a single place.
export function isDarkBasemap(id: BasemapStyleId): boolean {
	return id.includes('dark') || id === 'openfreemap-fiord';
}
