// Sorts a finished blend's files into the few a user actually reads and the
// pipeline intermediates (pickle twins of CSVs, per-year CV coefficients,
// staged inputs, logs), so the results page isn't a wall of filenames.

type Named = { filename: string };

export type OutputGroupKey = 'cv' | 'final' | 'final_day' | 'spec' | 'other';

export type OutputGroup<T extends Named> = {
	key: OutputGroupKey;
	label: string;
	files: T[];
};

const GROUPS: { key: Exclude<OutputGroupKey, 'other'>; label: string; matches: RegExp }[] = [
	{
		key: 'cv',
		label: 'Cross-validation scores',
		matches: /^(summary_models_pooled|summary_models|yearly_metrics_global).*\.csv$/
	},
	{
		key: 'final',
		label: 'Week-level blend weights',
		matches: /^coefs_blended_model_global_final\./
	},
	{
		key: 'final_day',
		label: 'Day-level blend model',
		matches: /^forest_blended_forest_global_final\./
	},
	{ key: 'spec', label: 'Training settings', matches: /^training_spec\.ya?ml$/ }
];

function groupFor(filename: string): OutputGroupKey {
	return GROUPS.find((g) => g.matches.test(filename))?.key ?? 'other';
}

/** Groups in display order; empty groups are dropped. */
export function groupBlendOutputs<T extends Named>(files: T[]): OutputGroup<T>[] {
	const all = [...GROUPS, { key: 'other' as const, label: 'Other files' }];
	return all
		.map((g) => ({
			key: g.key,
			label: g.label,
			files: files.filter((f) => groupFor(f.filename) === g.key)
		}))
		.filter((g) => g.files.length > 0);
}

export type YearlyScore = {
	year: number;
	model: string;
	brier: number | null;
	rps: number | null;
	auc: number | null;
};

function finite(value: string | undefined): number | null {
	const n = Number(value);
	return value === undefined || value === '' || !Number.isFinite(n) ? null : n;
}

/**
 * Parses `yearly_metrics_global*.csv`: one row per (holdout year, model) with
 * that year's CV Brier score, RPS and AUC. Sorted by year, then model.
 */
export function parseYearlyScores(csv: string): YearlyScore[] {
	const [headerLine, ...lines] = csv.trim().split(/\r?\n/);
	if (!headerLine) return [];
	const header = headerLine.split(',');
	const col = (name: string) => header.indexOf(name);
	const [year, model, brier, rps, auc] = ['year', 'model', 'brier', 'rps', 'auc'].map(col);
	if (year < 0 || model < 0) return [];
	return lines
		.map((line) => line.split(','))
		.map((cells) => ({
			year: Number(cells[year]),
			model: cells[model],
			brier: finite(cells[brier]),
			rps: finite(cells[rps]),
			auc: finite(cells[auc])
		}))
		.filter((row) => Number.isInteger(row.year) && row.model)
		.sort((a, b) => a.year - b.year || a.model.localeCompare(b.model));
}
