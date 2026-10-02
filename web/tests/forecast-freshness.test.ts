import { describe, expect, it } from 'vitest';
import { forecastFreshness } from '../src/lib/forecast-freshness';

const weekly = ['2026-05-01', '2026-05-08', '2026-05-15', '2026-05-22'];

describe('forecastFreshness', () => {
	it('reports the newest issue date regardless of input order', () => {
		const result = forecastFreshness([...weekly].reverse(), new Date('2026-05-25T12:00:00Z'));
		expect(result).toEqual({ latest: '2026-05-22', behind: false });
	});

	it('flags data that has fallen behind its own cadence this season', () => {
		const result = forecastFreshness(weekly, new Date('2026-06-20T00:00:00Z'));
		expect(result?.behind).toBe(true);
	});

	it('allows at least a week before calling daily data behind', () => {
		const daily = ['2026-05-01', '2026-05-02', '2026-05-03'];
		expect(forecastFreshness(daily, new Date('2026-05-08T00:00:00Z'))?.behind).toBe(false);
		expect(forecastFreshness(daily, new Date('2026-05-12T00:00:00Z'))?.behind).toBe(true);
	});

	it('never calls a past season behind', () => {
		const result = forecastFreshness(['2025-06-01', '2025-06-08'], new Date('2026-05-01'));
		expect(result?.behind).toBe(false);
	});

	it('returns null without issue dates', () => {
		expect(forecastFreshness([], new Date())).toBeNull();
	});
});
