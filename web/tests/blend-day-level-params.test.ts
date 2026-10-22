import { describe, expect, it } from 'vitest';

import {
	dayLevelFits,
	dayLevelParamsBody,
	dayLevelParamsError,
	dayLevelRunNote
} from '../src/routes/blends/day-level-params';

const blank = { yearsPerFold: '', trees: '', maxDepth: '', minSamplesLeaf: '', maxSamples: '' };

describe('day-level blend settings', () => {
	it('sends nothing when left blank, so the forest keeps its own settings', () => {
		expect(dayLevelParamsError(blank)).toBeNull();
		expect(dayLevelParamsBody(blank)).toBeUndefined();
	});

	it('sends only the settings filled in', () => {
		const input = { ...blank, yearsPerFold: '5', maxDepth: '12', maxSamples: '0.5' };

		expect(dayLevelParamsError(input)).toBeNull();
		expect(dayLevelParamsBody(input)).toEqual({
			years_per_fold: 5,
			max_depth: 12,
			max_samples: 0.5
		});
	});

	it('refuses what the server would refuse', () => {
		expect(dayLevelParamsError({ ...blank, yearsPerFold: '0' })).toMatch(/Years held out per fold/);
		expect(dayLevelParamsError({ ...blank, trees: '2.5' })).toMatch(/Trees/);
		expect(dayLevelParamsError({ ...blank, trees: '5000' })).toMatch(/10 to 500/);
		expect(dayLevelParamsError({ ...blank, maxDepth: 'deep' })).toMatch(/Deepest tree/);
		expect(dayLevelParamsError({ ...blank, maxSamples: '1.2' })).toMatch(/Share of rows/);
		expect(dayLevelParamsError({ ...blank, maxSamples: '0' })).toMatch(/Share of rows/);
	});

	it('counts one forest per true holdout, per block of CV years, and the final fit', () => {
		expect(dayLevelFits(20, 5, 1)).toBe(26);
		expect(dayLevelFits(20, 5, 5)).toBe(10);
		expect(dayLevelFits(20, 0, 3)).toBe(8);
	});

	it('warns that leaving one year out takes a while, and what blocks cost instead', () => {
		expect(dayLevelRunNote(26, 1)).toMatch(/26 forests.*hours/s);
		expect(dayLevelRunNote(10, 5)).toMatch(/not directly comparable/);
	});
});
