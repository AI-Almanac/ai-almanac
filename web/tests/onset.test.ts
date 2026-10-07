import { describe, it, expect } from 'vitest';
import {
	DAILY_BINS,
	WEEKLY_BINS,
	binDateLabel,
	consensusOnsetDay,
	dayToIso,
	isoToDay,
	laterStartDay,
	onsetHasPassed,
	probScaleMax,
	rampColor,
	windowColor,
	windowGradient
} from '../src/lib/onset';

const ISSUE = '2025-05-01';
const issueDay = isoToDay(ISSUE);

function oneHot(count: number, idx: number): number[] {
	return Array.from({ length: count }, (_, i) => (i === idx ? 1 : 0));
}

describe('onset bins', () => {
	it('starts Week 1 the day after issue and ends Week 4 at issue + 28', () => {
		expect(WEEKLY_BINS.dayRange(ISSUE, 0)).toEqual({ start: issueDay + 1, end: issueDay + 7 });
		expect(WEEKLY_BINS.dayRange(ISSUE, 3)).toEqual({ start: issueDay + 22, end: issueDay + 28 });
	});

	it('maps day d to the calendar date issue + d', () => {
		expect(dayToIso(DAILY_BINS.dayRange(ISSUE, 0).start)).toBe('2025-05-02');
		expect(DAILY_BINS.dayRange(ISSUE, 27)).toEqual({ start: issueDay + 28, end: issueDay + 28 });
	});

	it('puts "Later" last, starting at issue + 29 for both binnings', () => {
		expect(WEEKLY_BINS.count).toBe(5);
		expect(DAILY_BINS.count).toBe(29);
		expect(WEEKLY_BINS.labels[WEEKLY_BINS.laterIndex]).toBe('Later');
		expect(DAILY_BINS.labels[DAILY_BINS.laterIndex]).toBe('Later');
		expect(laterStartDay(ISSUE)).toBe(issueDay + 29);
	});

	it('describes each bin on the calendar', () => {
		expect(binDateLabel(WEEKLY_BINS, ISSUE, 0)).toBe('May 2–May 8');
		expect(binDateLabel(DAILY_BINS, ISSUE, 2)).toBe('May 4');
		expect(binDateLabel(DAILY_BINS, ISSUE, DAILY_BINS.laterIndex)).toBe('after May 29');
	});
});

describe('consensusOnsetDay', () => {
	it('is null when all mass sits in the undated "Later" bin', () => {
		expect(consensusOnsetDay(WEEKLY_BINS, [ISSUE], [[0, 0, 0, 0, 1]])).toBeNull();
		expect(consensusOnsetDay(DAILY_BINS, [ISSUE], [oneHot(29, 28)])).toBeNull();
	});

	it('is null when there is no forecast mass at all', () => {
		expect(consensusOnsetDay(WEEKLY_BINS, [ISSUE], [[0, 0, 0, 0, 0]])).toBeNull();
	});

	it('lands on the Week 1 midpoint (issue + 4) when all mass is in Week 1', () => {
		expect(consensusOnsetDay(WEEKLY_BINS, [ISSUE], [[1, 0, 0, 0, 0]])).toBe(issueDay + 4);
	});

	it('lands on the exact day when all mass is on one day', () => {
		expect(consensusOnsetDay(DAILY_BINS, [ISSUE], [oneHot(29, 9)])).toBe(issueDay + 10);
	});

	it('weights days across forecasts by their probability', () => {
		const later = '2025-05-08';
		const day = consensusOnsetDay(
			DAILY_BINS,
			[ISSUE, later],
			[oneHot(29, 0), oneHot(29, 0).map((p) => p * 3)]
		);
		// issue+1 at weight 1, (issue+7)+1 at weight 3.
		expect(day).toBe(issueDay + 1 + (3 * 7) / 4);
	});
});

describe('onsetHasPassed', () => {
	const onset = isoToDay('2025-06-05');

	it('is false on the onset day and within the ±3d grace band', () => {
		expect(onsetHasPassed('2025-06-05', onset)).toBe(false);
		expect(onsetHasPassed('2025-06-08', onset)).toBe(false); // exactly onset + 3
	});

	it('is true once a forecast is issued past the grace band', () => {
		expect(onsetHasPassed('2025-06-09', onset)).toBe(true);
		expect(onsetHasPassed('2025-07-15', onset)).toBe(true);
	});

	it('is false when the cell never dated an onset', () => {
		expect(onsetHasPassed('2025-07-15', null)).toBe(false);
	});
});

describe('probScaleMax', () => {
	const rows = [
		[...Array(28).fill(0.01), 0.72],
		[0.064, ...Array(27).fill(0), 0.9]
	];

	it('keeps the full 0–100% scale for weeks', () => {
		expect(probScaleMax(WEEKLY_BINS, [[0.1, 0.1, 0.1, 0.1, 0.6]])).toBe(1);
	});

	it('scales days to the peak daily chance, rounded up to a whole percent', () => {
		expect(probScaleMax(DAILY_BINS, rows)).toBe(0.07);
	});

	it('keeps the full scale when "Later" is the shown bin', () => {
		expect(probScaleMax(DAILY_BINS, rows, DAILY_BINS.laterIndex)).toBe(1);
	});
});

describe('windowColor', () => {
	it('runs the plasma ramp from soonest (vivid) to "Later" (dim)', () => {
		expect(windowColor(WEEKLY_BINS, 0)).toBe(rampColor(1));
		expect(windowColor(DAILY_BINS, DAILY_BINS.laterIndex)).toBe(rampColor(0));
		expect(windowColor(WEEKLY_BINS, 0, true)).toBe(rampColor(0));
	});
});

// Normalizes '#rrggbb' and 'rgb(r, g, b)' so colors from either source compare.
function rgb(color: string): number[] {
	if (color.startsWith('#')) return [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
	return (color.match(/\d+/g) ?? []).map(Number);
}

function gradientEnds(gradient: string): [number[], number[]] {
	const stops = gradient.match(/#[0-9a-f]{6}|rgb\([^)]*\)/gi) ?? [];
	return [rgb(stops[0]), rgb(stops[stops.length - 1])];
}

describe('windowGradient', () => {
	it('runs from the soonest bin color on the left to the Later color on the right', () => {
		for (const reversed of [false, true]) {
			const [left, right] = gradientEnds(windowGradient(reversed));
			expect(left).toEqual(rgb(windowColor(DAILY_BINS, 0, reversed)));
			expect(right).toEqual(rgb(windowColor(DAILY_BINS, DAILY_BINS.laterIndex, reversed)));
		}
	});
});
