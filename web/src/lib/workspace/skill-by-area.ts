/**
 * A benchmark's skill by named area, shaped into the table a forecaster reads:
 * one row per area, and for each measure every model beside the climatology baseline.
 */
import type { AreaLevel, AreaSkill, AreaSkillRow, SkillByArea } from '$lib/api/skill-by-area';
import { modelDisplayName } from '$lib/model-names';
import { isActiveStatus } from '$lib/run-status';
import { percent } from './area-table';
import { sortRows, type TableSort } from './table-sort';

export type MeasureKey = 'onset_error_days' | 'false_alarm_rate' | 'miss_rate';

export type ZoneMeasure = { key: MeasureKey; label: string; format: (value: number) => string };

/** Lower is better for all three. */
export const ZONE_MEASURES: ZoneMeasure[] = [
	{ key: 'onset_error_days', label: 'Onset error', format: (value) => `${value.toFixed(1)} d` },
	{ key: 'false_alarm_rate', label: 'False alarms', format: percent },
	{ key: 'miss_rate', label: 'Misses', format: percent }
];

export type ZoneCell = { text: string; value: number | null; best: boolean };

export type ZoneRow = {
	area: string;
	parent: string | null;
	/** How many grid cells the row's values rest on, in words. */
	cells: string;
	/** One list per measure, in ZONE_MEASURES order; each holds one cell per column. */
	measures: ZoneCell[][];
};

export type ZoneTable = {
	/** Model names, then the baseline when the run has one. */
	columns: string[];
	rows: ZoneRow[];
	notCovered: string[];
};

const BASELINE_COLUMN = 'Climatology';

/** Which areas to ask for; the empty choice is the run's own. */
export const AREA_CHOICES: { value: AreaLevel | ''; label: string }[] = [
	{ value: '', label: 'The run’s own areas' },
	{ value: 'adm1', label: 'Regions' },
	{ value: 'adm2', label: 'Zones' }
];

export function zoneWindow(data: SkillByArea, preferred?: string | null): string | null {
	return data.windows.find((window) => window === preferred) ?? data.windows[0] ?? null;
}

/** `12 cells`, or `8–12 cells` when the models were scored on different cells. */
export function cellsWords(counts: number[]): string {
	if (counts.length === 0) return 'no cells';
	const [low, high] = [Math.min(...counts), Math.max(...counts)];
	if (low !== high) return `${low}–${high} cells`;
	return `${low} ${low === 1 ? 'cell' : 'cells'}`;
}

function measureCells(measure: ZoneMeasure, skills: (AreaSkill | null)[]): ZoneCell[] {
	const values = skills.map((skill) => skill?.[measure.key] ?? null);
	const known = values.filter((value): value is number => value !== null);
	const best = known.length > 0 ? Math.min(...known) : null;
	return values.map((value) => ({
		value,
		text: value === null ? '–' : measure.format(value),
		best: value !== null && value === best
	}));
}

function areaKey(row: Pick<AreaSkillRow, 'area' | 'parent'>): string {
	return `${row.parent ?? ''}/${row.area}`;
}

/** The table for one lead window. The best value in each row and measure is marked, ties included. */
export function zoneTable(data: SkillByArea, window: string | null): ZoneTable {
	const inWindow = data.rows.filter((row) => row.window === window);
	const hasBaseline = inWindow.some((row) => row.climatology !== null);
	const byArea = new Map<string, AreaSkillRow[]>();
	for (const row of inWindow) byArea.set(areaKey(row), [...(byArea.get(areaKey(row)) ?? []), row]);

	const rows = [...byArea.values()].map((areaRows): ZoneRow => {
		const perModel = data.models.map(
			(model) => areaRows.find((row) => row.model === model) ?? null
		);
		const baseline = areaRows.find((row) => row.climatology)?.climatology ?? null;
		const skills = hasBaseline ? [...perModel, baseline] : perModel;
		return {
			area: areaRows[0].area,
			parent: areaRows[0].parent,
			cells: cellsWords(perModel.flatMap((row) => (row ? [row.cells] : []))),
			measures: ZONE_MEASURES.map((measure) => measureCells(measure, skills))
		};
	});
	return {
		columns: [...data.models.map(modelDisplayName), ...(hasBaseline ? [BASELINE_COLUMN] : [])],
		rows,
		notCovered: data.not_covered
	};
}

/** One measure of the table: an area per row, a model (then the baseline) per value. */
export type MeasureRow = Pick<ZoneRow, 'area' | 'parent' | 'cells'> & { values: ZoneCell[] };

/** Rows ordered by one column's values, the worst (highest) first to begin with; null leaves the areas as they came. */
export type ZoneSort = TableSort<number>;

/** The table narrowed to one measure, which is what fits beside the stage's other panes. */
export function measureRows(table: ZoneTable, measure: MeasureKey, sort: ZoneSort): MeasureRow[] {
	const index = ZONE_MEASURES.findIndex((each) => each.key === measure);
	const rows = table.rows.map(({ area, parent, cells, measures }) => ({
		area,
		parent,
		cells,
		values: measures[index] ?? []
	}));
	return sortRows(rows, sort, (row, column) => row.values[column]?.value ?? null);
}

function listed(names: string[]): string {
	if (names.length < 2) return names.join('');
	return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
}

/** Enough of a run's job to say why its model has no column. */
export type ZoneModelJob = { model_name: string | null; status: string };

/**
 * Why a model of the run has no column: its job stopped short, or has not finished
 * yet. Null when every model that could be in the table is.
 */
export function missingModelsNote(jobs: ZoneModelJob[], shownModels: string[]): string | null {
	const shown = new Set(shownModels.map((model) => model.toLowerCase()));
	const missing = jobs.filter(
		(job) => job.model_name && job.status !== 'complete' && !shown.has(job.model_name.toLowerCase())
	);
	const names = (running: boolean) =>
		missing
			.filter((job) => isActiveStatus(job.status) === running)
			.map((job) => modelDisplayName(job.model_name ?? ''));
	const [stopped, running] = [names(false), names(true)];
	const notes = [
		stopped.length === 1 && `${stopped[0]} is not shown: its run did not finish.`,
		stopped.length > 1 && `${listed(stopped)} are not shown: their runs did not finish.`,
		running.length === 1 && `${running[0]} is not shown yet: it is still running.`,
		running.length > 1 && `${listed(running)} are not shown yet: they are still running.`
	].filter(Boolean);
	return notes.length > 0 ? notes.join(' ') : null;
}

/** What the rows are and what a value means, said once under the table. */
export function zoneNote(data: SkillByArea): string {
	const areas =
		data.areas_from === 'area_of_interest'
			? 'These are the areas this run was scored on.'
			: 'These are the administrative areas of the run’s region.';
	return `${areas} Each value is the mean over the grid cells centred in the area; an area resting on few cells deserves less confidence. Bold marks the best value in each row, climatology included. Select a column heading to sort by it.`;
}
