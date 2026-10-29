import { describe, expect, it } from 'vitest';

import {
	ONSET_DEFINITIONS,
	effectiveOnsetDefinition,
	effectiveWindowDays,
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

	it('sends the dry-spell check only when switched on, with the numbers typed', () => {
		expect(onsetParamsBody({ ...blank, dryDays: '5' })).toEqual({});
		expect(onsetParamsBody({ ...blank, dryCheck: true })).toEqual({ dry_spell_check: true });
		expect(
			onsetParamsBody({ ...blank, dryCheck: true, dryDays: ' 5 ', dryWindowDays: '15' })
		).toEqual({ dry_spell_check: true, dry_spell_days: 5, dry_spell_window_days: 15 });
	});

	it('flags dry-spell numbers that are not whole days or cannot fit', () => {
		expect(onsetParamsError({ ...blank, dryCheck: true, dryDays: '2.5' })).toMatch(/whole/);
		expect(onsetParamsError({ ...blank, dryCheck: true, dryWindowDays: '5' })).toMatch(
			/7-day dry spell cannot fit in 5 days/
		);
		expect(onsetParamsError({ ...blank, dryCheck: true, dryDays: '5' })).toBeNull();
	});

	it("checks a search start against the definition's reference onset", () => {
		const india = { ...blank, region: 'india' };
		expect(onsetParamsError({ ...india, cutoffMonthDay: '06-02' })).toBeNull();
		expect(onsetParamsError({ ...india, cutoffMonthDay: '06-03' })).toMatch(/06-02 is before/);
	});

	it('sends a chosen wet window and describes the rule for it', () => {
		expect(onsetParamsBody({ ...blank, windowDays: '3' })).toEqual({ onset_window_days: 3 });
		expect(onsetParamsBody({ ...blank, windowDays: '' })).toEqual({});
		expect(effectiveWindowDays(blank)).toBe(5);
		expect(effectiveWindowDays({ ...blank, windowDays: '3' })).toBe(3);
		// ICPAC's 3 rain days stay: over 3 days that is every day wet.
		expect(ONSET_DEFINITIONS.icpac.summary(5)).toMatch(/5-day window .* at least 3 rain days/);
		expect(ONSET_DEFINITIONS.icpac.summary(3)).toMatch(/3 days in a row, each a rain day/);
		expect(ONSET_DEFINITIONS.moron_robertson_mok.summary(3)).toMatch(/mean 3-day rainfall/);
	});
});
