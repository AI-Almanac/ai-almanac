import { describe, expect, it } from 'vitest';

import {
	clampAside,
	effectiveAside,
	COMPARE_MIN_PX,
	MIN_ASIDE_PX
} from '$lib/components/split-width';

const PAGE = 1920;

describe('assistant panel width', () => {
	it('honours a dragged width between the bounds', () => {
		expect(clampAside(520, PAGE)).toBe(520);
	});

	it('refuses to shrink below a readable width', () => {
		expect(clampAside(10, PAGE)).toBe(MIN_ASIDE_PX);
		expect(clampAside(-500, PAGE)).toBe(MIN_ASIDE_PX);
	});

	it('keeps the results dominant', () => {
		// 60% of the page is the most the assistant may take.
		expect(clampAside(5000, PAGE)).toBe(1152);
	});

	it('still yields a usable width in a narrow container', () => {
		// The floor wins over the fraction, so the panel never collapses to nothing.
		expect(clampAside(300, 500)).toBe(MIN_ASIDE_PX);
	});

	it('gives an open comparison its own floor without saving it', () => {
		// A previously dragged 24rem would put two answers at 12rem each.
		expect(effectiveAside(384, PAGE, true)).toBe(COMPARE_MIN_PX);
		// A wider saved width is respected as-is.
		expect(effectiveAside(800, PAGE, true)).toBe(800);
		// And outside a comparison the saved width is untouched.
		expect(effectiveAside(384, PAGE, false)).toBe(384);
	});
});
