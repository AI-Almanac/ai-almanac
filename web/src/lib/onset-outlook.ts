import { argmax, binDateLabel, onsetHasPassed, type OnsetBins } from '$lib/onset';

// What one cell's season of forecasts says, shaped for the cell inspector.
// The estimated onset comes from consensusOnsetDay; a forecast issued after it
// still answers "when will onset begin?", which no longer applies there.

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
