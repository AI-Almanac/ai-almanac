import { describe, it, expect } from 'vitest';

import type { DataSource } from '../src/lib/api';
import {
	computeCoverage,
	defaultSplit,
	memberCountWarning,
	parseYearSpec,
	yearSpecError
} from '../src/routes/blends/year-coverage';

function source(start: number, end: number): DataSource {
	return { metadata: { start_year: start, end_year: end } } as unknown as DataSource;
}

describe('parseYearSpec', () => {
	it('expands ranges and lists', () => {
		expect(parseYearSpec('2008:2010,2012')).toEqual([2008, 2009, 2010, 2012]);
	});
	it('returns [] for empty and null for malformed', () => {
		expect(parseYearSpec('')).toEqual([]);
		expect(parseYearSpec('20x8')).toBeNull();
	});
});

describe('computeCoverage', () => {
	it('intersects sources and reserves a climatology runway', () => {
		// obs 1998-2012, models 2000-2012 -> earliest forecast 2008 (1998+10)
		const cov = computeCoverage(source(1998, 2012), [source(2000, 2012), source(2000, 2012)]);
		expect(cov).toEqual({ start: 2000, end: 2012, earliestForecast: 2008 });
	});
	it('is null when year metadata is missing', () => {
		expect(computeCoverage(undefined, [source(2000, 2012)])).toBeNull();
	});
});

describe('defaultSplit', () => {
	it('cross-validates within the full training span', () => {
		expect(defaultSplit({ start: 2000, end: 2012, earliestForecast: 2008 })).toEqual({
			training: '2008:2012',
			cv: '2008:2012'
		});
	});
	it('handles a single valid forecast year', () => {
		expect(defaultSplit({ start: 2000, end: 2008, earliestForecast: 2008 })).toEqual({
			training: '2008',
			cv: '2008'
		});
	});
	it('is null when no forecast year has enough runway', () => {
		expect(defaultSplit({ start: 2000, end: 2007, earliestForecast: 2008 })).toBeNull();
	});
	it('produces a split that passes validation', () => {
		const cov = { start: 2000, end: 2012, earliestForecast: 2008 };
		const split = defaultSplit(cov)!;
		expect(yearSpecError(cov, split.training, split.cv, '', '')).toBeNull();
	});
});

describe('memberCountWarning', () => {
	it('stays quiet for one or two models', () => {
		expect(memberCountWarning(1)).toBeNull();
		expect(memberCountWarning(2)).toBeNull();
	});
	it('warns about overfitting from three models up', () => {
		expect(memberCountWarning(3)).toMatch(/overfitting/);
		expect(memberCountWarning(5)).toContain('Blending 5 models');
	});
});

describe('yearSpecError', () => {
	const cov = { start: 2000, end: 2012, earliestForecast: 2008 };
	it('rejects a forecast start without enough climatology runway', () => {
		// This is the config that failed the real run.
		expect(yearSpecError(cov, '2000:2010', '2011,2012', '', '')).toMatch(/Climatology needs/);
	});
	it('rejects years outside shared coverage', () => {
		expect(yearSpecError(cov, '2008:2013', '', '', '')).toMatch(/only share data/);
	});
	it('accepts the default split', () => {
		expect(yearSpecError(cov, '2008:2010', '2011:2012', '', '')).toBeNull();
	});
	it('accepts identical training and CV holdout specs', () => {
		expect(yearSpecError(cov, '2008:2012', '2008:2012', '', '')).toBeNull();
	});
});
