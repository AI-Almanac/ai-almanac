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
			words: '8° to 12° lat, 36° to 40° lon'
		},
		{ case: 'is all of the data when no place is set', area: null, words: 'All of the data' }
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
