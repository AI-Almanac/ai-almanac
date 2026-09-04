// Client-side checks for the blend form's onset-definition fields. Mirrors
// `onset_param_errors` in server/services/job_submission.py so the form can
// refuse before the server does; the server remains the authority.

// Workflow defaults, used as placeholders. Mirror `build_lat_lon_intermediates_bundle`
// in modal/blending_app.py.
export const ONSET_DEFAULTS = {
	threshold_mm: 20,
	cutoff_month_day: '05-01',
	ref_onset_month_day: '06-01'
} as const;

export type OnsetParamsInput = {
	thresholdMm: string;
	cutoffMonthDay: string;
	refOnsetMonthDay: string;
};

export type OnsetParams = {
	threshold_mm?: number;
	cutoff_month_day?: string;
	ref_onset_month_day?: string;
};

// A real calendar MM-DD; leap day allowed (validated against a leap year).
export function isMonthDay(value: string): boolean {
	const m = value.match(/^(\d{2})-(\d{2})$/);
	if (!m) return false;
	const d = new Date(Date.UTC(2000, +m[1] - 1, +m[2]));
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
	// MM-DD strings compare lexically in calendar order.
	if (cutoff && refOnset && refOnset < cutoff)
		return 'Reference onset date is before the onset search start; onset cannot be detected before the search begins.';
	return null;
}

// Only fields the user actually filled in; blanks fall back to the workflow defaults.
export function onsetParamsBody(input: OnsetParamsInput): OnsetParams {
	const out: OnsetParams = {};
	const threshold = input.thresholdMm.trim();
	const cutoff = input.cutoffMonthDay.trim();
	const refOnset = input.refOnsetMonthDay.trim();
	if (threshold) out.threshold_mm = Number(threshold);
	if (cutoff) out.cutoff_month_day = cutoff;
	if (refOnset) out.ref_onset_month_day = refOnset;
	return out;
}
