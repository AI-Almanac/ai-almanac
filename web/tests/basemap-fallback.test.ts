/**
 * Choosing the bundled basemap when an online one cannot be had.
 *
 * Every map draws its results on top of the basemap style, so a machine with no
 * internet used to get an empty frame. These tests pin when the maps give up on
 * the online style, and that what they fall back to needs no network at all.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BASEMAP_WAIT_MS, fallBackWhenBasemapFails } from '$lib/basemap-fallback';
import {
	BASEMAP_STYLES,
	BUNDLED_BASEMAP,
	DEFAULT_BASEMAP,
	basemapStyle,
	isDarkBasemap
} from '$lib/basemaps';

type Handler = (event: object) => void;

class FakeMap {
	handlers = new Map<string, Set<Handler>>();
	setStyle = vi.fn();
	on(event: string, handler: Handler) {
		this.handlers.set(event, (this.handlers.get(event) ?? new Set()).add(handler));
		return this;
	}
	off(event: string, handler: Handler) {
		this.handlers.get(event)?.delete(handler);
		return this;
	}
	emit(event: string, payload: object = {}) {
		for (const handler of [...(this.handlers.get(event) ?? [])]) handler(payload);
	}
}

function watch() {
	const map = new FakeMap();
	const onFallback = vi.fn();
	const stop = fallBackWhenBasemapFails(map as never, onFallback);
	return { map, onFallback, stop };
}

const bundledStyle = basemapStyle(BUNDLED_BASEMAP);

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('fallBackWhenBasemapFails', () => {
	it('switches to the bundled basemap when the style request fails', () => {
		const { map, onFallback } = watch();
		map.emit('error', { error: new Error('Failed to fetch') });

		expect(map.setStyle).toHaveBeenCalledExactlyOnceWith(bundledStyle, { diff: false });
		expect(onFallback).toHaveBeenCalledOnce();
	});

	it('switches to the bundled basemap when the style request hangs', () => {
		const { map, onFallback } = watch();
		vi.advanceTimersByTime(BASEMAP_WAIT_MS - 1);
		expect(map.setStyle).not.toHaveBeenCalled();

		vi.advanceTimersByTime(1);
		expect(map.setStyle).toHaveBeenCalledExactlyOnceWith(bundledStyle, { diff: false });
		expect(onFallback).toHaveBeenCalledOnce();
	});

	it('keeps an online style that loaded, even if it later errors or is slow to finish', () => {
		const { map, onFallback } = watch();
		map.emit('style.load');
		map.emit('error', { error: new Error('sprite failed') });
		vi.advanceTimersByTime(BASEMAP_WAIT_MS * 2);

		expect(map.setStyle).not.toHaveBeenCalled();
		expect(onFallback).not.toHaveBeenCalled();
	});

	it('ignores tile and data errors, which only happen once a style has parsed', () => {
		const { map, onFallback } = watch();
		map.emit('error', { error: new Error('tile 404'), sourceId: 'carto' });

		expect(map.setStyle).not.toHaveBeenCalled();
		expect(onFallback).not.toHaveBeenCalled();
	});

	it('falls back only once, however many failures follow', () => {
		const { map, onFallback } = watch();
		map.emit('error', { error: new Error('Failed to fetch') });
		map.emit('error', { error: new Error('Failed to fetch') });
		vi.advanceTimersByTime(BASEMAP_WAIT_MS);

		expect(map.setStyle).toHaveBeenCalledOnce();
		expect(onFallback).toHaveBeenCalledOnce();
	});

	it('does nothing after the map stops watching, as on teardown', () => {
		const { map, onFallback, stop } = watch();
		stop();
		map.emit('error', { error: new Error('Failed to fetch') });
		vi.advanceTimersByTime(BASEMAP_WAIT_MS);

		expect(map.setStyle).not.toHaveBeenCalled();
		expect(onFallback).not.toHaveBeenCalled();
	});
});

describe('bundled basemap', () => {
	it('is offered as "No background map"', () => {
		expect(BASEMAP_STYLES.find((s) => s.id === BUNDLED_BASEMAP)?.label).toBe('No background map');
	});

	it('requests nothing from another host', () => {
		const style = JSON.stringify(bundledStyle);
		expect(style).not.toMatch(/https?:\/\//);
		expect(style).not.toMatch(/"(glyphs|sprite)"/);
	});

	it('is dark, like the default, so the colour scales read the same on it', () => {
		expect(isDarkBasemap(BUNDLED_BASEMAP)).toBe(isDarkBasemap(DEFAULT_BASEMAP));
	});
});
