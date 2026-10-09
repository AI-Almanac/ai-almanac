import { describe, expect, it } from 'vitest';
import type { DataSource } from '../src/lib/api';
import { describeSourceCoverage, gridsMatch, missingYears } from '../src/lib/source-coverage';

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
	it('matches equal grid steps', () => {
		expect(gridsMatch(0.25, 0.25)).toBe(true);
	});

	it('rejects a model on a different grid than the observations', () => {
		expect(gridsMatch(0.25, 2)).toBe(false);
	});

	it('does not rule out a source whose grid step was never recorded', () => {
		expect(gridsMatch(null, 2)).toBe(true);
		expect(gridsMatch(0.25, undefined)).toBe(true);
	});
});
