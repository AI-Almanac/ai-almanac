/**
 * A gridded result file in a forecaster's words: what it is, and what it holds
 * under names they would use, with each per-year series folded into one entry.
 */
import { modelDisplayName } from '$lib/model-names';
import { parseFileSummary, type FileVariable } from './file-summary';

export type GriddedEntry = {
	label: string;
	unit: string | null;
	/** The file's own names for what this entry covers. */
	variables: string[];
};

export type GriddedFile = { description: string; entries: GriddedEntry[] };

type Measure = { label: string; unit?: string };

const MEASURES: Record<string, Measure> = {
	false_alarm_rate: { label: 'False alarm rate' },
	miss_rate: { label: 'Missed onsets' },
	mean_mae: { label: 'Onset error, average', unit: 'days' },
	brier_score: { label: 'Brier score' },
	brier_skill_score: { label: 'Brier skill score' },
	ranked_probability_score: { label: 'Ranked probability score' },
	ranked_probability_skill_score: { label: 'Ranked probability skill score' },
	auc: { label: 'Area under the ROC curve' },
	reliability: { label: 'Reliability' },
	rmse: { label: 'Rainfall error, root mean square', unit: 'mm' },
	mae: { label: 'Rainfall error, average', unit: 'mm' },
	bias: { label: 'Rainfall bias', unit: 'mm' },
	acc: { label: 'Anomaly correlation' }
};

/** What a series of one variable per year measures, keyed by the name before the year. */
const YEARLY_MEASURES: Record<string, Measure> = {
	mae: { label: 'Onset error', unit: 'days' }
};

const YEARLY = /^(.+)_(\d{4})$/;

function words(name: string): string {
	const text = name.replace(/[_\-]+/g, ' ').trim() || name;
	return text.charAt(0).toUpperCase() + text.slice(1);
}

function measureOf(variable: FileVariable): Measure {
	const known = MEASURES[variable.name.toLowerCase()];
	return {
		label: known?.label ?? variable.description ?? words(variable.name),
		unit: known?.unit ?? variable.units ?? undefined
	};
}

function entryOf(variable: FileVariable): GriddedEntry {
	const { label, unit } = measureOf(variable);
	return { label, unit: unit ?? null, variables: [variable.name] };
}

function yearlyEntry(series: string, members: FileVariable[]): GriddedEntry {
	const measure = YEARLY_MEASURES[series.toLowerCase()] ?? { label: words(series) };
	const years = members.map((member) => Number(YEARLY.exec(member.name)![2]));
	return {
		label: `${measure.label} by year, ${Math.min(...years)}–${Math.max(...years)}`,
		unit: measure.unit ?? members[0].units ?? null,
		variables: members.map((member) => member.name)
	};
}

function seriesOf(variable: FileVariable): string | null {
	return YEARLY.exec(variable.name)?.[1] ?? null;
}

/** Each variable under a readable name, in file order; a per-year series is one entry. */
function griddedEntries(variables: FileVariable[]): GriddedEntry[] {
	const members = (series: string) => variables.filter((each) => seriesOf(each) === series);
	const seen = new Set<string>();
	return variables.flatMap((variable) => {
		const series = seriesOf(variable);
		if (!series || members(series).length < 2) return [entryOf(variable)];
		if (seen.has(series)) return [];
		seen.add(series);
		return [yearlyEntry(series, members(series))];
	});
}

const SPATIAL_METRICS = /^(e2s_)?spatial_metrics_(.+)_(\d+-\d+|all)$/;
const ONSET_CLIMATOLOGY = /^climatology_onset_doy_(\d{4})-(\d{4})$/;

function subject(model: string): string {
	return model.toLowerCase() === 'climatology'
		? 'The climatology baseline'
		: modelDisplayName(model);
}

function leadWindow(window: string): string {
	return window === 'all' ? 'at every lead time' : `${window.replace('-', '–')} days ahead`;
}

/** What the file is, read from the name the run gave it. */
function describe(label: string): string {
	const spatial = SPATIAL_METRICS.exec(label);
	if (spatial) {
		const [, rainfall, model, window] = spatial;
		const who = `${subject(model)}, forecasts ${leadWindow(window)}`;
		return rainfall
			? `${who}: how close its rainfall came to what was observed, at each grid point.`
			: `${who}: how close its onset dates came to the observed onset, and how often it raised a false alarm or missed an onset, at each grid point.`;
	}
	const climatology = ONSET_CLIMATOLOGY.exec(label);
	if (climatology) {
		return `The usual onset day of the year at each grid point, from ${climatology[1]}–${climatology[2]}.`;
	}
	return 'Gridded data, with one value at each grid point.';
}

/** Null for a file with no gridded variables to describe. */
export function griddedFile(file: {
	label: string;
	summary: Record<string, unknown>;
}): GriddedFile | null {
	const { variables } = parseFileSummary(file.summary);
	if (variables.length === 0) return null;
	return { description: describe(file.label), entries: griddedEntries(variables) };
}
