import { describe, expect, it } from 'vitest';

import { isMonthDay, onsetParamsBody, onsetParamsError } from '../src/routes/blends/onset-params';

const blank = { thresholdMm: '', cutoffMonthDay: '', refOnsetMonthDay: '' };

describe('blend onset params', () => {
	it('accepts real MM-DD dates only', () => {
		expect(isMonthDay('05-01')).toBe(true);
		expect(isMonthDay('02-29')).toBe(true);
		expect(isMonthDay('02-30')).toBe(false);
		expect(isMonthDay('13-01')).toBe(false);
		expect(isMonthDay('5-1')).toBe(false);
		expect(isMonthDay('2024-05-01')).toBe(false);
	});

	it('treats all-blank input as valid and sends nothing', () => {
		expect(onsetParamsError(blank)).toBeNull();
		expect(onsetParamsBody(blank)).toEqual({});
	});

	it('flags a non-positive or non-numeric threshold', () => {
		expect(onsetParamsError({ ...blank, thresholdMm: '0' })).toMatch(/positive/);
		expect(onsetParamsError({ ...blank, thresholdMm: '-2' })).toMatch(/positive/);
		expect(onsetParamsError({ ...blank, thresholdMm: 'twenty' })).toMatch(/positive/);
		expect(onsetParamsError({ ...blank, thresholdMm: ' 25.5 ' })).toBeNull();
	});

	it('flags malformed dates and a reference date before the search start', () => {
		expect(onsetParamsError({ ...blank, cutoffMonthDay: '5/1' })).toMatch(/MM-DD/);
		expect(onsetParamsError({ ...blank, refOnsetMonthDay: '06-31' })).toMatch(/MM-DD/);
		expect(
			onsetParamsError({ ...blank, cutoffMonthDay: '06-15', refOnsetMonthDay: '06-01' })
		).toMatch(/before the onset search start/);
		expect(
			onsetParamsError({ ...blank, cutoffMonthDay: '05-01', refOnsetMonthDay: '06-01' })
		).toBeNull();
	});

	it('only sends fields the user filled in, trimmed and typed', () => {
		expect(
			onsetParamsBody({ thresholdMm: ' 25 ', cutoffMonthDay: '', refOnsetMonthDay: '06-10' })
		).toEqual({ threshold_mm: 25, ref_onset_month_day: '06-10' });
	});
});
