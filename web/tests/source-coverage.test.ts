import { describe, expect, it } from 'vitest';
import type { DataSource } from '../src/lib/api';
import {
	describeSourceCoverage,
	gridsMatch,
	missingYears,
	scoredTogether
} from '../src/lib/source-coverage';

function source(metadata: Record<string, unknown>): DataSource {
	return { metadata } as unknown as DataSource;
}

describe('describeSourceCoverage', () => {
	it('summarizes years, gaps and grid', () => {
		const src = source({
			start_year: 2001,
			end_year: 2020,
			missing_years: [2012],
			grid_step_deg: 0.25
		});
		expect(describeSourceCoverage(src)).toBe('2001–2020 · missing 2012 · 0.25° grid');
	});

	it('treats sources without a gap list as gap-free and skips unknown parts', () => {
		const src = source({ start_year: 2016, end_year: 2016 });
		expect(missingYears(src)).toEqual([]);
		expect(describeSourceCoverage(src)).toBe('2016');
	});

	it('returns null when nothing was detected', () => {
		expect(describeSourceCoverage(source({}))).toBeNull();
	});
});

describe('gridsMatch', () => {
	it.each([
		{ case: 'matches equal grid steps', model: 0.25, obs: 0.25, matches: true },
		{
			case: 'rejects a model on a different grid than the observations',
			model: 0.25,
			obs: 2,
			matches: false
		},
		{
			case: 'does not rule out a source with no recorded model grid',
			model: null,
			obs: 2,
			matches: true
		},
		{
			case: 'does not rule out a source with no recorded observation grid',
			model: 0.25,
			obs: undefined,
			matches: true
		}
	])('$case', ({ model, obs, matches }) => {
		expect(gridsMatch(model, obs)).toBe(matches);
	});
});

describe('which forecast sources go with which observations', () => {
	const ETHIOPIA = { lat_min: 3, lat_max: 15, lon_min: 33, lon_max: 48 };
	const EAST_AFRICA = { lat_min: -12, lat_max: 18, lon_min: 28, lon_max: 52 };
	const INDIA = { lat_min: 6, lat_max: 36, lon_min: 68, lon_max: 98 };

	function located(region: string | null, bounds?: object): DataSource {
		return {
			region,
			metadata: bounds ? { spatial_bounds: bounds } : {}
		} as unknown as DataSource;
	}

	it('offers an archive wherever it has data in common, whatever it was filed under', () => {
		expect(scoredTogether(located('ethiopia', ETHIOPIA), located(null, EAST_AFRICA))).toBe(true);
	});

	it('does not offer a source filed under the same region whose data lies elsewhere', () => {
		expect(scoredTogether(located('ethiopia', ETHIOPIA), located('ethiopia', INDIA))).toBe(false);
	});

	it('matches a source of unknown extent by the region it was filed under', () => {
		const observations = located('ethiopia', ETHIOPIA);

		expect(scoredTogether(observations, located('ethiopia'))).toBe(true);
		expect(scoredTogether(observations, located('india'))).toBe(false);
		expect(scoredTogether(observations, located(null))).toBe(false);
	});
});
