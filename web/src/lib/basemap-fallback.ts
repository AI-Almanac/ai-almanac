import type { Map as MapLibreMap, StyleSpecification } from 'maplibre-gl';

import { BUNDLED_BASEMAP, basemapStyle } from '$lib/basemaps';

/** Long enough for a slow link, short enough that an offline user is not left staring. */
export const BASEMAP_WAIT_MS = 8_000;

type StyleHost = Pick<MapLibreMap, 'on' | 'off' | 'setStyle'>;

/** maplibre reports a failed request as an AJAXError carrying the URL it asked for. */
function isFailureOf(url: string, event: object): boolean {
	const error = (event as { error?: { url?: unknown } }).error;
	return error?.url === url;
}

/**
 * Swap to the bundled basemap if a remote style just requested never parses.
 *
 * Every map adds its data layers on top of the basemap style, so a style JSON
 * that fails (no internet, a blocked CDN) or hangs leaves the results undrawn.
 * Call this with the style just passed to the Map constructor or setStyle; it
 * watches only until that style loads. Only a failure of that style's own
 * request counts: tile, sprite or glyph errors, including late ones from the
 * style being replaced, never trigger the swap. A style object is already in
 * hand, so there is nothing to watch.
 *
 * Returns a function that stops watching, for map teardown.
 */
export function fallBackWhenBasemapFails(
	map: StyleHost,
	style: string | StyleSpecification,
	onFallback: () => void,
	waitMs = BASEMAP_WAIT_MS
): () => void {
	if (typeof style !== 'string') return () => {};
	const stop = () => {
		clearTimeout(timer);
		map.off('style.load', stop);
		map.off('error', onError);
	};
	const fallBack = () => {
		stop();
		map.setStyle(basemapStyle(BUNDLED_BASEMAP), { diff: false });
		onFallback();
	};
	const onError = (event: object) => {
		if (isFailureOf(style, event)) fallBack();
	};
	const timer = setTimeout(fallBack, waitMs);
	map.on('style.load', stop);
	map.on('error', onError);
	return stop;
}
