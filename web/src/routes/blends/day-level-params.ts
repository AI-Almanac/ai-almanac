// Client-side checks for the blend form's day-level blend settings. Mirrors
// `DayLevelSettings` in server/services/job_submission.py so the form can
// refuse before the server does; the server remains the authority.

import type { DayLevelSettings } from '$lib/api/blends';

// The forest's own settings, used as placeholders and sent only when changed.
export const DAY_LEVEL_DEFAULTS = {
	years_per_fold: 1,
	trees: 100,
	min_samples_leaf: 20,
	max_samples: 1
} as const;

export type DayLevelInput = {
	yearsPerFold: string;
	trees: string;
	maxDepth: string;
	minSamplesLeaf: string;
	maxSamples: string;
};

const INTEGER_FIELDS: {
	key: keyof DayLevelInput;
	setting: keyof DayLevelSettings;
	label: string;
	min: number;
	max: number;
}[] = [
	{
		key: 'yearsPerFold',
		setting: 'years_per_fold',
		label: 'Years held out per fold',
		min: 1,
		max: 10
	},
	{ key: 'trees', setting: 'trees', label: 'Trees', min: 10, max: 500 },
	{ key: 'maxDepth', setting: 'max_depth', label: 'Deepest tree', min: 1, max: 64 },
	{
		key: 'minSamplesLeaf',
		setting: 'min_samples_leaf',
		label: 'Fewest rows per leaf',
		min: 1,
		max: 1000
	}
];

// First problem found, or null when every provided field is acceptable.
export function dayLevelParamsError(input: DayLevelInput): string | null {
	for (const field of INTEGER_FIELDS) {
		const raw = input[field.key].trim();
		if (!raw) continue;
		const n = Number(raw);
		if (!Number.isInteger(n) || n < field.min || n > field.max)
			return `${field.label} must be a whole number from ${field.min} to ${field.max}.`;
	}
	const share = input.maxSamples.trim();
	if (share) {
		const n = Number(share);
		if (!Number.isFinite(n) || n <= 0 || n > 1)
			return 'Share of rows per tree must be more than 0 and at most 1.';
	}
	return null;
}

// Only the settings the user filled in, or undefined when none: the run then
// trains with the forest's own settings.
export function dayLevelParamsBody(input: DayLevelInput): DayLevelSettings | undefined {
	const out: DayLevelSettings = {};
	for (const field of INTEGER_FIELDS) {
		const raw = input[field.key].trim();
		if (raw) out[field.setting] = Number(raw);
	}
	const share = input.maxSamples.trim();
	if (share) out.max_samples = Number(share);
	return Object.keys(out).length ? out : undefined;
}

// Forests the day-level blend fits: one per true holdout, one per block of CV
// years, and the final fit on every training year.
export function dayLevelFits(
	cvYears: number,
	trueHoldoutYears: number,
	yearsPerFold: number
): number {
	return trueHoldoutYears + Math.ceil(cvYears / Math.max(yearsPerFold, 1)) + 1;
}

// What the chosen cross-validation costs, shown before the blend is trained.
export function dayLevelRunNote(fits: number, yearsPerFold: number): string {
	if (yearsPerFold <= 1)
		return (
			`Leaving one year out fits ${fits} forests. On large regions each fit can take ` +
			'many minutes, so the day-level blend may train for hours after the week-level ' +
			'results are ready. Holding several years out per fold is faster.'
		);
	return (
		`Holding ${yearsPerFold} years out per fold fits ${fits} forests. Each fold trains on ` +
		'fewer years, so day-level skill is not directly comparable to the week-level ' +
		"blend's, which leaves one year out."
	);
}
