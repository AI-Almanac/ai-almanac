import { describe, it, expect } from 'vitest';

import type { DataSource } from '../src/lib/api';
import {
	computeCoverage,
	coverageLimits,
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
		expect(cov).toEqual({ start: 2000, end: 2012, earliestForecast: 2008, missing: [] });
	});
	it('collects years any chosen source is missing', () => {
		const gappy = {
			metadata: { start_year: 2000, end_year: 2012, missing_years: [2005] }
		} as unknown as DataSource;
		expect(computeCoverage(source(1990, 2012), [gappy, source(2000, 2012)])?.missing).toEqual([
			2005
		]);
	});
	it('is null when year metadata is missing', () => {
		expect(computeCoverage(undefined, [source(2000, 2012)])).toBeNull();
	});
});

describe('defaultSplit', () => {
	it('cross-validates within the full training span', () => {
		expect(defaultSplit({ start: 2000, end: 2012, earliestForecast: 2008, missing: [] })).toEqual({
			training: '2008:2012',
			cv: '2008:2012',
			trueHoldout: ''
		});
	});
	it('handles a single valid forecast year', () => {
		expect(defaultSplit({ start: 2000, end: 2008, earliestForecast: 2008, missing: [] })).toEqual({
			training: '2008',
			cv: '2008',
			trueHoldout: ''
		});
	});
	it('is null when no forecast year has enough runway', () => {
		expect(
			defaultSplit({ start: 2000, end: 2007, earliestForecast: 2008, missing: [] })
		).toBeNull();
	});
	it('produces a split that passes validation', () => {
		const cov = { start: 1990, end: 2023, earliestForecast: 2000, missing: [] };
		const split = defaultSplit(cov)!;
		expect(yearSpecError(cov, split.training, split.cv, '', split.trueHoldout)).toBeNull();
	});
	it('holds out the most recent fifth of the years', () => {
		expect(defaultSplit({ start: 1990, end: 2023, earliestForecast: 2000, missing: [] })).toEqual({
			training: '2000:2019',
			cv: '2000:2019',
			trueHoldout: '2020:2023'
		});
	});
	it('never holds out so much that training drops below the minimum', () => {
		expect(defaultSplit({ start: 1990, end: 2010, earliestForecast: 2000, missing: [] })).toEqual({
			training: '2000:2009',
			cv: '2000:2009',
			trueHoldout: '2010'
		});
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
	it('rejects a true holdout year that is also trained on', () => {
		const cov = { start: 1990, end: 2023, earliestForecast: 2000, missing: [] };
		expect(yearSpecError(cov, '2000:2020', '2000:2020', '', '2020:2023')).toMatch(/remove 2020/);
	});
	const cov = { start: 2000, end: 2012, earliestForecast: 2008, missing: [] };
	it('rejects a forecast start without enough climatology runway', () => {
		// This is the config that failed the real run.
		expect(yearSpecError(cov, '2000:2010', '2011,2012', '', '')).toMatch(
			/climatology baseline needs/
		);
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
	it('rejects forecast years a chosen source is missing', () => {
		const gappy = { ...cov, missing: [2010] };
		expect(yearSpecError(gappy, '2008:2012', '', '', '')).toMatch(/no data for 2010/);
		expect(yearSpecError(gappy, '2008,2009,2011', '', '', '')).toBeNull();
	});
});

describe('coverageLimits', () => {
	function named(name: string, start: number, end: number): DataSource {
		return { name, metadata: { start_year: start, end_year: end } } as unknown as DataSource;
	}

	it('names the sources that narrow the shared range', () => {
		const obs = named('Observations', 1990, 2022);
		const models = [named('Model A', 2000, 2022), named('Model B', 1995, 2018)];
		const limits = coverageLimits(obs, models, computeCoverage(obs, models)!);
		expect(limits.start.map((s) => s.name)).toEqual(['Model A']);
		expect(limits.end.map((s) => s.name)).toEqual(['Model B']);
	});

	it('names every source that ties for the limiting year', () => {
		const obs = named('Observations', 1990, 2022);
		const models = [named('Model A', 2000, 2022), named('Model B', 2000, 2022)];
		const limits = coverageLimits(obs, models, computeCoverage(obs, models)!);
		expect(limits.start.map((s) => s.name)).toEqual(['Model A', 'Model B']);
		expect(limits.end).toEqual([]);
	});

	it('names nothing when every source shares the same range', () => {
		const obs = named('Observations', 2000, 2020);
		const models = [named('Model A', 2000, 2020)];
		expect(coverageLimits(obs, models, computeCoverage(obs, models)!)).toEqual({
			start: [],
			end: []
		});
	});
});

describe('defaultSplit with a raised training minimum', () => {
	it('holds out fewer years so training still meets the enforced minimum', () => {
		expect(
			defaultSplit({ start: 1990, end: 2023, earliestForecast: 2000, missing: [] }, 22)
		).toEqual({ training: '2000:2021', cv: '2000:2021', trueHoldout: '2022:2023' });
	});
});
