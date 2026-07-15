// Shared coordinate formatting for every map surface, so the lat/lon shown in
// tooltips and inspectors reads identically across pages.

// One signed axis as an absolute value + hemisphere suffix, e.g. 22.00°N.
export function formatCoord(
	value: number,
	positive: string,
	negative: string,
	digits = 2
): string {
	return `${Math.abs(value).toFixed(digits)}°${value >= 0 ? positive : negative}`;
}

// A lat/lon pair, e.g. "22.00°N 78.00°E".
export function formatLatLon(lat: number, lon: number, separator = ' '): string {
	return `${formatCoord(lat, 'N', 'S')}${separator}${formatCoord(lon, 'E', 'W')}`;
}
