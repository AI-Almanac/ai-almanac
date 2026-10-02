import { describe, expect, it } from 'vitest';
import { latestIssueDate } from '../src/lib/forecast-freshness';

describe('latestIssueDate', () => {
	it('reports the newest issue date regardless of input order or time suffix', () => {
		expect(latestIssueDate(['2026-05-22T00:00:00', '2026-05-01', '2026-05-08'])).toBe('2026-05-22');
	});

	it('ignores unparseable dates', () => {
		expect(latestIssueDate(['not-a-date', '2026-05-01'])).toBe('2026-05-01');
	});

	it('returns null without issue dates', () => {
		expect(latestIssueDate([])).toBeNull();
	});
});
