import { describe, expect, it } from 'vitest';

import { draftIn, studyAreaWords } from '../src/lib/workspace/study-area';

describe('the place a workspace works in', () => {
	it.each([
		{
			case: 'is named by its places',
			area: { level: 'adm1' as const, units: ['Amhara', 'Tigray'], iso: 'ETH' },
			words: 'Amhara, Tigray'
		},
		{
			case: 'is named by its bounds when it is a box',
			area: { lat_min: 8, lat_max: 12, lon_min: 36, lon_max: 40 },
			words: 'Box 8–12°N, 36–40°E'
		},
		{
			case: 'says which side of the equator each edge of a box is on',
			area: { lat_min: -5, lat_max: 3, lon_min: -10, lon_max: -2 },
			words: 'Box 5°S–3°N, 10–2°W'
		},
		{
			case: 'counts the places past the first few rather than naming them all',
			area: {
				level: 'adm2' as const,
				units: ['Awi', 'Bale', 'Borena', 'Gedeo', 'Guji'],
				iso: 'ETH'
			},
			words: 'Awi, Bale, Borena +2 more'
		},
		{ case: 'is not set when no place is chosen', area: null, words: 'Not set' }
	])('$case', ({ area, words }) => {
		expect(studyAreaWords(area)).toBe(words);
	});
});

describe('a new run with nothing else to start from', () => {
	const kenya = { level: 'adm0' as const, units: ['Kenya'], iso: 'KEN' };

	it('starts in the workspace area, with everything else left to choose', () => {
		expect(draftIn(kenya)).toEqual({ model_ids: [], focus_area: kenya });
	});

	it('starts blank in a workspace with no area', () => {
		expect(draftIn(null)).toBeUndefined();
	});
});
