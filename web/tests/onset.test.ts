import { describe, it, expect } from 'vitest';
import { consensusOnsetDay, onsetHasPassed, isoToDay } from '../src/lib/onset';

describe('consensusOnsetDay', () => {
	it('is null when all mass sits in the undated "Later" bucket', () => {
		expect(consensusOnsetDay(['2025-05-01'], [[0, 0, 0, 0, 1]])).toBeNull();
	});

	it('is null when there is no forecast mass at all', () => {
		expect(consensusOnsetDay(['2025-05-01'], [[0, 0, 0, 0, 0]])).toBeNull();
	});

	it('lands on the Week 1 midpoint (issue + 3) when all mass is in Week 1', () => {
		const day = consensusOnsetDay(['2025-05-01'], [[1, 0, 0, 0, 0]]);
		expect(day).toBe(isoToDay('2025-05-01') + 3);
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
