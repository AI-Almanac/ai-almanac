import type { Map as MapLibreMap } from 'maplibre-gl';

import { BUNDLED_BASEMAP, basemapStyle } from '$lib/basemaps';

/** Long enough for a slow link, short enough that an offline user is not left staring. */
export const BASEMAP_WAIT_MS = 8_000;

type StyleHost = Pick<MapLibreMap, 'on' | 'off' | 'setStyle'>;

/**
 * Swap to the bundled basemap if the style just requested never parses.
 *
 * Every map adds its data layers on top of the basemap style, so a style JSON
 * that fails (no internet, a blocked CDN) or hangs leaves the results undrawn.
 * Call this right after constructing a map or calling setStyle; it watches only
 * until that style loads. Errors tagged with a source are tile or data failures
 * of a style that already parsed, so they never trigger the swap.
 *
 * Returns a function that stops watching, for map teardown.
 */
export function fallBackWhenBasemapFails(
	map: StyleHost,
	onFallback: () => void,
	waitMs = BASEMAP_WAIT_MS
): () => void {
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
		if (!('sourceId' in event)) fallBack();
	};
	const timer = setTimeout(fallBack, waitMs);
	map.on('style.load', stop);
	map.on('error', onError);
	return stop;
}
