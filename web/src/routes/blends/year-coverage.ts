import type { DataSource } from '$lib/api';
import { sourceYears, type YearSpan } from '$lib/source-coverage';

// Climatology needs this many observation years before the first forecast year.
// Mirrors `min_onset_years` in modal/blending_app.py.
export const MIN_ONSET_YEARS = 10;

export type Coverage = {
	start: number;
	end: number;
	earliestForecast: number;
};

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
	const obsSpan = sourceYears(obs);
	if (obsSpan == null || models.length === 0) return null;
	const modelSpans = models.map(sourceYears);
	if (modelSpans.some((span) => span == null)) return null;
	const modelStarts = (modelSpans as YearSpan[]).map((span) => span.start);
	const modelEnds = (modelSpans as YearSpan[]).map((span) => span.end);
	return {
		start: Math.max(obsSpan.start, ...modelStarts),
		end: Math.min(obsSpan.end, ...modelEnds),
		earliestForecast: Math.max(obsSpan.start + MIN_ONSET_YEARS, ...modelStarts)
	};
}

export type CoverageLimits = {
	// Sources whose own first/last year sets the shared range while another
	// chosen source reaches further — the ones to swap for a wider range.
	start: DataSource[];
	end: DataSource[];
};

export function coverageLimits(
	obs: DataSource | undefined,
	models: DataSource[],
	cov: Coverage
): CoverageLimits {
	const spans = [obs, ...models].flatMap((source) => {
		const span = sourceYears(source);
		return source && span ? [{ source, span }] : [];
	});
	const reachesEarlier = spans.some(({ span }) => span.start < cov.start);
	const reachesLater = spans.some(({ span }) => span.end > cov.end);
	return {
		start: reachesEarlier
			? spans.filter(({ span }) => span.start === cov.start).map(({ source }) => source)
			: [],
		end: reachesLater
			? spans.filter(({ span }) => span.end === cov.end).map(({ source }) => source)
			: []
	};
}

// Default split: train on every valid forecast year and cross-validate within
// that same span (each year is scored while held out of its own fit) rather than
// reserving a short tail. null when no valid forecast year exists (not enough
// observation runway).
export function defaultSplit(cov: Coverage): { training: string; cv: string } | null {
	const lo = cov.earliestForecast;
	const hi = cov.end;
	if (lo > hi) return null;
	const years = lo === hi ? `${lo}` : `${lo}:${hi}`;
	return { training: years, cv: years };
}

// Blends with this many members start fitting noise: the weights have more
// freedom than a handful of training years can pin down.
export const OVERFIT_WARNING_MEMBERS = 3;

// Advice, not a rule — callers must not block submission on this.
export function memberCountWarning(modelCount: number): string | null {
	if (modelCount < OVERFIT_WARNING_MEMBERS) return null;
	return (
		`Blending ${modelCount} models risks overfitting: the more models in the blend, the more ` +
		'its weights fit the quirks of the training years instead of real skill, so the scores you ' +
		'get back can look better than the blend will do on a new season. This matters most when ' +
		'you have only a few training years — two models is the safer starting point.'
	);
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
		return `The climatology baseline needs at least ${MIN_ONSET_YEARS} observed years before the first forecast year — start at ${cov.earliestForecast} or later.`;
	return null;
}
