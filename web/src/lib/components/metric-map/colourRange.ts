/**
 * A colour scale that one extreme cell cannot stretch: its ends sit where the bulk
 * of the values do, and whatever lies beyond takes the end colour.
 */
import type { JobGridResponse } from '$lib/api';

export type ColourRange = {
	min: number;
	max: number;
	/** Some values lie below `min` and are drawn in the first colour. */
	clippedLow: boolean;
	/** Some values lie above `max` and are drawn in the last colour. */
	clippedHigh: boolean;
};

const LOW_PERCENTILE = 0.02;
const HIGH_PERCENTILE = 0.98;
/** Too few values to tell an outlier from the spread. */
const FEWEST_VALUES = 10;
/**
 * How far past the percentiles the scale still reaches, as a share of the span
 * between them: a value just outside is ordinary and keeps its own colour.
 */
const SLACK = 0.25;

/** Every scored cell's value across the grids. */
export function scoredValues(grids: Pick<JobGridResponse, 'values'>[]): number[] {
	return grids.flatMap((grid) =>
		grid.values.flatMap((row) => row.filter((value): value is number => value != null))
	);
}

/** The scale's ends for these values; null when there are none. */
export function robustRange(values: number[]): ColourRange | null {
	const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
	if (sorted.length === 0) return null;
	const [lowest, highest] = [sorted[0], sorted[sorted.length - 1]];
	const full = { min: lowest, max: highest, clippedLow: false, clippedHigh: false };
	if (sorted.length < FEWEST_VALUES) return full;

	const last = sorted.length - 1;
	const low = sorted[Math.ceil(LOW_PERCENTILE * last)];
	const high = sorted[Math.floor(HIGH_PERCENTILE * last)];
	const reach = (high - low) * SLACK;
	if (reach <= 0) return full;

	const min = sorted.find((value) => value >= low - reach) ?? low;
	const max = sorted.findLast((value) => value <= high + reach) ?? high;
	return { min, max, clippedLow: min > lowest, clippedHigh: max < highest };
}

/** A scale end in words: `≥ 25.0 days` when values beyond it share its colour. */
export function scaleEnd(text: string, end: 'low' | 'high', clipped: boolean | undefined): string {
	if (!clipped) return text;
	return end === 'low' ? `≤ ${text}` : `≥ ${text}`;
}
