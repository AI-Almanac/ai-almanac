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

export type BasemapChoice = { id: BasemapStyleId; label: string };

/** The few a reader chooses between, named by what they look like rather than who makes them. */
export const COMMON_BASEMAPS: BasemapChoice[] = [
	{ id: 'carto-light', label: 'Light' },
	{ id: 'carto-light-no-labels', label: 'Light, no place names' },
	{ id: 'carto-dark', label: 'Dark' }
];

/** The credit a picture of the map must carry. */
function basemapCredit(id: BasemapStyleId): string {
	return id.startsWith('carto')
		? 'Basemap © CARTO, © OpenStreetMap contributors'
		: 'Basemap © OpenMapTiles, © OpenStreetMap contributors';
}

/** The credits a picture of the map must carry: its basemap's, and the outlines' when shown. */
export function mapCredit(id: BasemapStyleId, showsBoundaries: boolean): string {
	return showsBoundaries ? `${basemapCredit(id)} · Boundaries: geoBoundaries` : basemapCredit(id);
}

// Dark styles want a lightened land + white dot outlines; light styles the
// opposite. One predicate keeps that decision in a single place.
export function isDarkBasemap(id: BasemapStyleId): boolean {
	return id.includes('dark') || id === 'openfreemap-fiord';
}
