import type { DataSource } from '$lib/api';
import { overlaps, parseBounds, type Bounds } from '$lib/geo/bbox';

// Years and grid a registered source covers, read from its detected metadata.

export type YearSpan = { start: number; end: number };

// A registered source or a validation draft — both carry detected metadata.
type HasMetadata = Pick<DataSource, 'metadata'>;

function metaNumber(source: HasMetadata | undefined, key: string): number | null {
	const value = source?.metadata?.[key];
	return typeof value === 'number' ? value : null;
}

export function sourceYears(source: HasMetadata | undefined): YearSpan | null {
	const start = metaNumber(source, 'start_year');
	const end = metaNumber(source, 'end_year');
	return start == null || end == null ? null : { start, end };
}

// Sources registered before gaps were detected have no list: unknown, not gap-free.
export function missingYears(source: HasMetadata | undefined): number[] {
	const value = source?.metadata?.missing_years;
	return Array.isArray(value) ? value.filter((y): y is number => typeof y === 'number') : [];
}

function formatYearSpan(span: YearSpan): string {
	return span.start === span.end ? `${span.start}` : `${span.start}–${span.end}`;
}

export function gridStep(source: HasMetadata | undefined): number | null {
	return metaNumber(source, 'grid_step_deg');
}

// Mirrors the server's grid_mismatch_errors: a source registered before grid
// steps were recorded has no step and is not ruled out.
export function gridsMatch(
	obsStep: number | null | undefined,
	modelStep: number | null | undefined
): boolean {
	if (obsStep == null || modelStep == null) return true;
	return Math.abs(obsStep - modelStep) <= 1e-9 * Math.max(Math.abs(obsStep), Math.abs(modelStep));
}

export function formatDegrees(step: number): string {
	return `${Number(step.toFixed(3))}°`;
}

function formatGridStep(source: HasMetadata | undefined): string | null {
	const step = gridStep(source);
	return step == null ? null : formatDegrees(step);
}

// One-line summary, e.g. "2001–2020 · missing 2012 · 0.25° grid".
export function describeSourceCoverage(source: HasMetadata | undefined): string | null {
	const span = sourceYears(source);
	const missing = missingYears(source);
	const grid = formatGridStep(source);
	const parts = [
		span ? formatYearSpan(span) : null,
		missing.length ? `missing ${missing.join(', ')}` : null,
		grid ? `${grid} grid` : null
	].filter((part): part is string => part !== null);
	return parts.length ? parts.join(' · ') : null;
}

type Located = Pick<DataSource, 'metadata' | 'region'>;

/** Where a source has data, as detected when it was validated. */
export function spatialBounds(source: HasMetadata | undefined): Bounds | null {
	return parseBounds(source?.metadata?.spatial_bounds);
}

/**
 * Whether a forecast source can be scored against observations: both have data
 * somewhere in common. A source whose extent was never detected is matched by the
 * region it was filed under. Mirrors services/coverage.py.
 */
export function scoredTogether(observations: Located, model: Located): boolean {
	const observed = spatialBounds(observations);
	const forecast = spatialBounds(model);
	if (observed && forecast) return overlaps(observed, forecast);
	return (observations.region ?? '') === (model.region ?? '');
}
