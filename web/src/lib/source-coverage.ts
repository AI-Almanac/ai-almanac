import type { DataSource } from '$lib/api';

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

export function formatYearSpan(span: YearSpan): string {
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

export function formatGridStep(source: HasMetadata | undefined): string | null {
	const step = gridStep(source);
	return step == null ? null : `${Number(step.toFixed(3))}°`;
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
