import { describe, expect, it } from 'vitest';
import { WEEKLY_BINS, isoToDay } from '../src/lib/onset';
import { consensusBand, forecastOutlook, splitAtOnset } from '../src/lib/onset-outlook';

const ONSET = isoToDay('2025-06-01');

describe('forecastOutlook', () => {
	it('names the most likely window on the calendar', () => {
		expect(forecastOutlook(WEEKLY_BINS, '2025-05-01', [0.1, 0.6, 0.1, 0.1, 0.1], ONSET)).toEqual({
			kind: 'forecast',
			mostLikely: 1,
			probability: 0.6,
			when: 'May 9–May 15'
		});
	});

	it('dates the open-ended window from the issue date', () => {
		const outlook = forecastOutlook(WEEKLY_BINS, '2025-05-01', [0, 0, 0, 0.2, 0.8], ONSET);
		expect(outlook).toMatchObject({ kind: 'forecast', mostLikely: 4, when: 'after May 29' });
	});

	it('does not present a forecast issued after the estimated onset as a forecast', () => {
		expect(forecastOutlook(WEEKLY_BINS, '2025-06-10', [0.9, 0.1, 0, 0, 0], ONSET)).toEqual({
			kind: 'after-onset'
		});
	});

	it('reports a forecast with no chance of onset as empty', () => {
		expect(forecastOutlook(WEEKLY_BINS, '2025-05-01', [0, 0, 0, 0, 0], null)).toEqual({
			kind: 'empty'
		});
	});
});

describe('splitAtOnset', () => {
	it('splits forecasts at the estimated onset plus its grace days', () => {
		const dates = ['2025-05-20', '2025-06-04', '2025-06-05', '2025-06-20'];
		expect(splitAtOnset(dates, ONSET)).toEqual({ before: [0, 1], after: [2, 3] });
	});

	it('keeps every forecast when no forecast ever dated the onset', () => {
		expect(splitAtOnset(['2025-05-20', '2025-07-01'], null)).toEqual({
			before: [0, 1],
			after: []
		});
	});
});

describe('consensusBand', () => {
	it('spans three days either side of the estimate', () => {
		expect(consensusBand(ONSET + 0.4)).toEqual({ start: ONSET - 3, end: ONSET + 3 });
	});

	it('is null without an estimate', () => {
		expect(consensusBand(null)).toBeNull();
	});
});
