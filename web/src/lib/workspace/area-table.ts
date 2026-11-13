/**
 * A forecast summarized by named areas (zones, states, woredas): one row per area
 * and issue date, read into the view a forecaster scans before a bulletin.
 */
import type { ArtifactProvenance, WorkspaceArtifact } from '$lib/api';
import { csvRecords } from './file-preview';
import { sortRows, type TableSort } from './table-sort';

/** The step that writes these tables; its presence is recorded, not guessed from headers. */
const AREA_SUMMARY_STEP = 'forecast.summarize_by_area';

export function isAreaSummary(file: Pick<WorkspaceArtifact, 'kind' | 'breadcrumb'>): boolean {
	return file.kind === 'table' && file.breadcrumb.at(-1) === AREA_SUMMARY_STEP;
}

/**
 * Whether the p_ columns are the share of each area one model run puts onset in,
 * rather than a chance. Recorded by the summarizing step; tables from before it
 * was recorded read as chances.
 */
export function valuesAreShares(provenance: ArtifactProvenance | null): boolean {
	return (
		provenance?.steps.some(
			(step) => step.primitive === AREA_SUMMARY_STEP && step.params?.single_run === true
		) ?? false
	);
}

export type WithinColumn = { key: string; days: number; label: string };

export type AreaRow = {
	area: string;
	parent: string | null;
	coverage: number | null;
	within: Record<string, number | null>;
	mostLikely: string | null;
	mostLikelyShare: number | null;
};

export type AreaTable = {
	/** Oldest first. */
	issueDates: string[];
	columns: WithinColumn[];
	rowsByDate: Map<string, AreaRow[]>;
};

const WITHIN = /^p_within_(\d+)d$/;

function number(value: string | undefined): number | null {
	if (value === undefined || value.trim() === '') return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

export function parseAreaTable(text: string): AreaTable {
	const [header = [], ...records] = csvRecords(text.replace(/^﻿/, ''));
	const at = (record: string[], name: string) => record[header.indexOf(name)];
	const columns = header.flatMap((name) => {
		const match = WITHIN.exec(name);
		return match ? [{ key: name, days: Number(match[1]), label: `Within ${match[1]} d` }] : [];
	});
	const rowsByDate = new Map<string, AreaRow[]>();
	for (const record of records) {
		const issueDate = at(record, 'issue_date');
		const area = at(record, 'area');
		if (!issueDate || !area) continue;
		const row: AreaRow = {
			area,
			parent: at(record, 'parent') || null,
			coverage: number(at(record, 'coverage')),
			within: Object.fromEntries(columns.map((c) => [c.key, number(at(record, c.key))])),
			mostLikely: at(record, 'most_likely') || null,
			mostLikelyShare: number(at(record, 'most_likely_share'))
		};
		rowsByDate.set(issueDate, [...(rowsByDate.get(issueDate) ?? []), row]);
	}
	return { issueDates: [...rowsByDate.keys()].sort(), columns, rowsByDate };
}

export function latestIssueDate(table: AreaTable): string | null {
	return table.issueDates.at(-1) ?? null;
}

/** The sort key of the area-name column. */
export const AREA_KEY = 'area';

/** The column a forecaster reads first: two weeks out when the table has it. */
export function defaultSortKey(table: AreaTable): string {
	return (table.columns.find((c) => c.days === 14) ?? table.columns[0])?.key ?? AREA_KEY;
}

/** Rows for one issue date, ordered by a within column or by area name; ties go by name. */
export function rowsOn(
	table: AreaTable,
	issueDate: string | null,
	sort: TableSort<string>
): AreaRow[] {
	const rows = issueDate ? (table.rowsByDate.get(issueDate) ?? []) : [];
	const byName = [...rows].sort((a, b) => a.area.localeCompare(b.area));
	return sortRows(byName, sort, (row, key) =>
		key === AREA_KEY ? row.area : (row.within[key] ?? null)
	);
}

/** `week2` → `Week 2`, `day_8` → `Day 8`, `later` → `Later`. */
export function windowLabel(window: string | null): string {
	if (!window) return '–';
	const match = /^(week|day)_?(\d+)$/.exec(window);
	if (match) return `${match[1] === 'week' ? 'Week' : 'Day'} ${match[2]}`;
	return window[0].toUpperCase() + window.slice(1);
}

export function percent(value: number | null): string {
	return value === null ? '–' : `${Math.round(value * 100)}%`;
}

/** What the numbers mean, said once under the table. */
export function valuesNote(shares: boolean): string {
	return shares
		? 'Values are the share of each area where this model run puts onset in the window; one run, not a chance.'
		: 'Values are the chance of onset within each window, averaged over the area.';
}
