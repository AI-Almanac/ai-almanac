import type { DataSource } from '$lib/api';

// Climatology needs this many observation years before the first forecast year.
// Mirrors `min_onset_years` in modal/blending_app.py.
export const MIN_ONSET_YEARS = 10;

export type Coverage = {
	start: number;
	end: number;
	earliestForecast: number;
};

function metaYear(source: DataSource | undefined, key: 'start_year' | 'end_year'): number | null {
	const value = source?.metadata?.[key];
	return typeof value === 'number' ? value : null;
}

// Parse a year spec ('2005:2010', '2011,2012') into sorted years. Mirrors the
// server's _parse_year_spec. Returns null on a malformed token so callers can
// report it instead of silently dropping years.
export function parseYearSpec(value: string): number[] | null {
	const years = new Set<number>();
	for (const part of value.split(',')) {
		const token = part.trim();
		if (!token) continue;
		const range = token.match(/^(\d{4}):(\d{4})$/);
		const single = token.match(/^(\d{4})$/);
		if (range) {
			for (let y = +range[1]; y <= +range[2]; y++) years.add(y);
		} else if (single) {
			years.add(+single[1]);
		} else {
			return null;
		}
	}
	return [...years].sort((a, b) => a - b);
}

// Years where every chosen source has data, plus the earliest forecast year that
// leaves a MIN_ONSET_YEARS observation runway for climatology.
export function computeCoverage(
	obs: DataSource | undefined,
	models: DataSource[]
): Coverage | null {
	const obsStart = metaYear(obs, 'start_year');
	const obsEnd = metaYear(obs, 'end_year');
	if (obsStart == null || obsEnd == null || models.length === 0) return null;
	const modelStarts = models.map((m) => metaYear(m, 'start_year'));
	const modelEnds = models.map((m) => metaYear(m, 'end_year'));
	if (modelStarts.some((y) => y == null) || modelEnds.some((y) => y == null)) return null;
	return {
		start: Math.max(obsStart, ...(modelStarts as number[])),
		end: Math.min(obsEnd, ...(modelEnds as number[])),
		earliestForecast: Math.max(obsStart + MIN_ONSET_YEARS, ...(modelStarts as number[]))
	};
}

// Default split: reserve the last 1-2 forecast years for CV holdout, train on the
// rest, all inside the valid forecast range. null when no valid forecast year
// exists (not enough observation runway).
export function defaultSplit(cov: Coverage): { training: string; cv: string } | null {
	const lo = cov.earliestForecast;
	const hi = cov.end;
	if (lo > hi) return null;
	const span = hi - lo + 1;
	if (span === 1) return { training: `${lo}`, cv: '' };
	const holdoutStart = hi - Math.min(2, span - 1) + 1;
	const trainEnd = holdoutStart - 1;
	return {
		training: lo === trainEnd ? `${lo}` : `${lo}:${trainEnd}`,
		cv: holdoutStart === hi ? `${hi}` : `${holdoutStart}:${hi}`
	};
}

// Validate user-entered specs against coverage. Returns an error message or null.
export function yearSpecError(
	cov: Coverage,
	training: string,
	cvHoldout: string,
	forecast: string,
	trueHoldout: string
): string | null {
	const specs = [training, cvHoldout, forecast, trueHoldout].map((s) => parseYearSpec(s.trim()));
	if (specs.some((s) => s === null)) return 'Years must look like "2005:2010" or "2011,2012".';
	const [train, cv, explicit, trueHold] = specs as number[][];
	const forecastYears = explicit.length ? explicit : [...train, ...cv, ...trueHold];
	if (forecastYears.length === 0) return null;
	const min = Math.min(...forecastYears);
	const max = Math.max(...forecastYears);
	if (min < cov.start || max > cov.end)
		return `Chosen sources only share data for ${cov.start}–${cov.end}.`;
	if (min < cov.earliestForecast)
		return `Climatology needs ${MIN_ONSET_YEARS} years of observations before the first forecast year — start at ${cov.earliestForecast} or later.`;
	return null;
}
