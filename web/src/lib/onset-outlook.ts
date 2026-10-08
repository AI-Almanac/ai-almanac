import { argmax, binDateLabel, onsetHasPassed, type OnsetBins } from '$lib/onset';

// What one cell's season of forecasts says, shaped for the cell inspector.
// The estimated onset comes from consensusOnsetDay; a forecast issued after it
// still answers "when will onset begin?", which no longer applies there.

// How consensusOnsetDay works, in the reader's terms. Shown wherever the
// estimate drives what's on screen, so the map and inspector explain it alike.
export const ONSET_ESTIMATE_EXPLANATION =
	'The likely onset date is the average of the dates the forecasts predicted, weighted by their chances. ' +
	'Chances of onset more than 4 weeks out have no date, so they don’t count. ' +
	'Forecasts issued after that date are left out and the average is taken again until it settles, ' +
	'because a forecast issued once the rains have begun still predicts onset just after its own issue date. ' +
	'It’s an estimate from the forecasts, not an observed onset.';

export type OnsetBand = { start: number; end: number };

const BAND_HALF_WIDTH_DAYS = 3;

export function consensusBand(consensusDay: number | null): OnsetBand | null {
	if (consensusDay == null) return null;
	return {
		start: Math.round(consensusDay - BAND_HALF_WIDTH_DAYS),
		end: Math.round(consensusDay + BAND_HALF_WIDTH_DAYS)
	};
}

export type ForecastOutlook =
	| { kind: 'forecast'; mostLikely: number; probability: number; when: string }
	| { kind: 'after-onset' }
	| { kind: 'empty' };

export function forecastOutlook(
	bins: OnsetBins,
	issueIso: string,
	row: number[],
	consensusDay: number | null
): ForecastOutlook {
	if (onsetHasPassed(issueIso, consensusDay)) return { kind: 'after-onset' };
	if (!row.some((p) => p > 0)) return { kind: 'empty' };
	const mostLikely = argmax(row);
	return {
		kind: 'forecast',
		mostLikely,
		probability: row[mostLikely],
		when: binDateLabel(bins, issueIso, mostLikely)
	};
}

// Forecast indices, in issue order, split at the estimated onset.
export function splitAtOnset(
	issueDates: string[],
	consensusDay: number | null
): { before: number[]; after: number[] } {
	const indices = issueDates.map((_, i) => i);
	const passed = (i: number) => onsetHasPassed(issueDates[i], consensusDay);
	return { before: indices.filter((i) => !passed(i)), after: indices.filter(passed) };
}
