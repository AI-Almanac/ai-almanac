import { describe, expect, it } from 'vitest';

import {
	effectiveOnsetDefinition,
	isMonthDay,
	onsetParamsBody,
	onsetParamsError,
	regionOnsetDefinition
} from '../src/routes/blends/onset-params';

// An ICPAC region's blank form.
const blank = {
	definition: '' as const,
	region: 'ethiopia',
	thresholdMm: '',
	cutoffMonthDay: '',
	refOnsetMonthDay: ''
};

describe('blend onset params', () => {
	it('accepts real MM-DD dates only', () => {
		expect(isMonthDay('05-01')).toBe(true);
		expect(isMonthDay('02-29')).toBe(false);
		expect(isMonthDay('02-28')).toBe(true);
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

	it('checks a single date override against the other field default', () => {
		expect(onsetParamsError({ ...blank, refOnsetMonthDay: '04-15' })).toMatch(
			/before the onset search start 05-01/
		);
		expect(onsetParamsError({ ...blank, cutoffMonthDay: '06-15' })).toMatch(/06-01 is before/);
		expect(onsetParamsError({ ...blank, cutoffMonthDay: '04-15' })).toBeNull();
	});

	it('only sends fields the user filled in, trimmed and typed', () => {
		expect(onsetParamsBody({ ...blank, thresholdMm: ' 25 ', refOnsetMonthDay: '06-10' })).toEqual({
			threshold_mm: 25,
			ref_onset_month_day: '06-10'
		});
	});

	it('uses modified Moron–Robertson in India and ICPAC elsewhere', () => {
		expect(regionOnsetDefinition('india')).toBe('moron_robertson_mok');
		expect(regionOnsetDefinition('India')).toBe('moron_robertson_mok');
		expect(regionOnsetDefinition('ethiopia')).toBe('icpac');
		expect(regionOnsetDefinition(null)).toBe('icpac');
		const india = effectiveOnsetDefinition({ definition: '', region: 'india' });
		expect([india.window_days, india.threshold_mm, india.ref_onset_month_day]).toEqual([
			5,
			null,
			'06-02'
		]);
	});

	it('sends a chosen definition, and nothing for the region default', () => {
		const india = { ...blank, region: 'india' };
		expect(onsetParamsBody({ ...india, definition: 'icpac' })).toEqual({
			onset_definition: 'icpac'
		});
		expect(onsetParamsBody(india)).toEqual({});
	});

	it("checks a search start against the definition's reference onset", () => {
		const india = { ...blank, region: 'india' };
		expect(onsetParamsError({ ...india, cutoffMonthDay: '06-02' })).toBeNull();
		expect(onsetParamsError({ ...india, cutoffMonthDay: '06-03' })).toMatch(/06-02 is before/);
	});
});
