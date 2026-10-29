// Client-side checks for the blend form's onset-definition fields. Mirrors
// `onset_param_errors` in server/services/job_submission.py so the form can
// refuse before the server does; the server remains the authority.

// The onset definitions a blend can use. Mirror ONSET_DEFINITIONS and
// REGION_ONSET_DEFINITIONS in src/ai_almanac/envs/blend_recipe.py.
export type OnsetDefinitionId = 'icpac' | 'moron_robertson_mok';

export type OnsetDefinition = {
	label: string;
	summary: string;
	// The trigger window in days, and its rainfall threshold; null when each
	// grid cell's threshold is computed from the observations.
	window_days: number;
	threshold_mm: number | null;
	ref_onset_month_day: string;
};

export const ONSET_DEFINITIONS: Record<OnsetDefinitionId, OnsetDefinition> = {
	icpac: {
		label: 'ICPAC',
		summary:
			'3 wet days (each at least 1 mm) totalling more than 20 mm, with no 5-day dry spell in the next 21 days.',
		window_days: 3,
		threshold_mm: 20,
		ref_onset_month_day: '06-01'
	},
	moron_robertson_mok: {
		label: 'Modified Moron–Robertson (MOK)',
		summary:
			"The first wet day (at least 1 mm) of the first 5-day spell totalling at least the grid cell's mean 5-day rainfall (April–October, 1991–2020), on or after 06-02, the median onset over Kerala; no dry-spell check.",
		window_days: 5,
		threshold_mm: null,
		ref_onset_month_day: '06-02'
	}
};

const REGION_ONSET_DEFINITIONS: Record<string, OnsetDefinitionId> = {
	india: 'moron_robertson_mok'
};

/** The definition a region's blends use unless one is chosen. */
export function regionOnsetDefinition(region: string | null | undefined): OnsetDefinitionId {
	return REGION_ONSET_DEFINITIONS[(region ?? '').trim().toLowerCase()] ?? 'icpac';
}

// Defaults for the fields left blank, used as placeholders.
export const ONSET_DEFAULTS = {
	cutoff_month_day: '05-01'
} as const;

export type OnsetParamsInput = {
	// '' for the region's definition.
	definition: OnsetDefinitionId | '';
	region: string | null | undefined;
	thresholdMm: string;
	cutoffMonthDay: string;
	refOnsetMonthDay: string;
};

export type OnsetParams = {
	onset_definition?: OnsetDefinitionId;
	threshold_mm?: number;
	cutoff_month_day?: string;
	ref_onset_month_day?: string;
};

/** The definition a blend with these inputs uses. */
export function effectiveOnsetDefinition(
	input: Pick<OnsetParamsInput, 'definition' | 'region'>
): OnsetDefinition {
	return ONSET_DEFINITIONS[input.definition || regionOnsetDefinition(input.region)];
}

// A calendar MM-DD that exists every year. Feb 29 is refused because each
// season builds a date from it, which fails in non-leap years.
export function isMonthDay(value: string): boolean {
	const m = value.match(/^(\d{2})-(\d{2})$/);
	if (!m) return false;
	const d = new Date(Date.UTC(2001, +m[1] - 1, +m[2]));
	return d.getUTCMonth() === +m[1] - 1 && d.getUTCDate() === +m[2];
}

// First problem found, or null when every provided field is acceptable.
export function onsetParamsError(input: OnsetParamsInput): string | null {
	const threshold = input.thresholdMm.trim();
	const cutoff = input.cutoffMonthDay.trim();
	const refOnset = input.refOnsetMonthDay.trim();

	if (threshold) {
		const n = Number(threshold);
		if (!Number.isFinite(n) || n <= 0)
			return 'Onset rainfall threshold must be a positive number of mm.';
	}
	if (cutoff && !isMonthDay(cutoff))
		return 'Onset search start must be a date in MM-DD form, e.g. 05-01.';
	if (refOnset && !isMonthDay(refOnset))
		return 'Reference onset date must be a date in MM-DD form, e.g. 06-01.';
	// Compare effective values so one override is checked against the other's
	// default. Zero-padded MM-DD strings compare lexically in calendar order.
	const effectiveCutoff = cutoff || ONSET_DEFAULTS.cutoff_month_day;
	const effectiveRefOnset = refOnset || effectiveOnsetDefinition(input).ref_onset_month_day;
	if (effectiveRefOnset < effectiveCutoff)
		return `Reference onset date ${effectiveRefOnset} is before the onset search start ${effectiveCutoff}; onset cannot be detected before the search begins.`;
	return null;
}

// Only fields the user actually filled in; blanks fall back to the definition's defaults.
export function onsetParamsBody(input: OnsetParamsInput): OnsetParams {
	const out: OnsetParams = {};
	if (input.definition) out.onset_definition = input.definition;
	const threshold = input.thresholdMm.trim();
	const cutoff = input.cutoffMonthDay.trim();
	const refOnset = input.refOnsetMonthDay.trim();
	if (threshold) out.threshold_mm = Number(threshold);
	if (cutoff) out.cutoff_month_day = cutoff;
	if (refOnset) out.ref_onset_month_day = refOnset;
	return out;
}
