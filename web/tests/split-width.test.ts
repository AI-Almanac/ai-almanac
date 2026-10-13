import { describe, expect, it } from 'vitest';

import {
	clampAside,
	effectiveAside,
	COMPARE_MIN_PX,
	MIN_ASIDE_PX
} from '$lib/components/split-width';

const PAGE = 1920;

describe('assistant panel width', () => {
	it.each([
		{
			case: 'honours a dragged width between the bounds',
			width: 520,
			container: PAGE,
			expected: 520
		},
		{
			case: 'refuses to shrink below a readable width',
			width: 10,
			container: PAGE,
			expected: MIN_ASIDE_PX
		},
		{ case: 'refuses a negative width', width: -500, container: PAGE, expected: MIN_ASIDE_PX },
		// 60% of the page is the most the assistant may take.
		{ case: 'keeps the results dominant', width: 5000, container: PAGE, expected: 1152 },
		// The floor wins over the fraction, so the panel never collapses to nothing.
		{
			case: 'still yields a usable width in a narrow container',
			width: 300,
			container: 500,
			expected: MIN_ASIDE_PX
		}
	])('$case', ({ width, container, expected }) => {
		expect(clampAside(width, container)).toBe(expected);
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
