/**
 * Shaping ROMP skill-score responses into chart series.
 *
 * Kept out of the Svelte component so it can be unit tested — uPlot needs real
 * canvas measurement and doesn't run under jsdom.
 */
import type { JobSkillScores, SkillBin } from '$lib/api';

export type LeadBin = {
	/** Bin midpoint in forecast days — the x position on a shared lead axis. */
	day: number;
	/** Display label, e.g. "1-5". */
	label: string;
};

/** Which numeric field of a bin a curve plots. */
export type SkillMetricKey = 'brier_skill_score' | 'auc';

/** One model's curve on a shared lead axis. */
export type SkillCurveSeries = {
	key: string;
	label: string;
	color: string;
	/** One value per entry in the chart's `leads`; null where the bin had no samples. */
	values: (number | null)[];
};

/**
 * Collect the distinct lead-time bins across every job, ordered by lead day.
 *
 * ROMP scores each verification window separately, so a run yields bins 1-5
 * through 11-15 from the 1-15 window and 16-20 through 26-30 from 16-30. They
 * don't overlap, so they compose onto a single 1-30 axis and a model reads as
 * one continuous curve rather than two disjoint segments.
 */
export function collectLeadBins(responses: JobSkillScores[]): LeadBin[] {
	const byLabel = new Map<string, LeadBin>();
	for (const response of responses) {
		for (const window of response.windows) {
			for (const bin of window.bins) {
				if (byLabel.has(bin.label)) continue;
				byLabel.set(bin.label, {
					day: (bin.lead_day_min + bin.lead_day_max) / 2,
					label: bin.label
				});
			}
		}
	}
	return [...byLabel.values()].sort((a, b) => a.day - b.day);
}

/**
 * Flatten a job's windows into one label→bin lookup.
 *
 * A model appears in at most one window per bin, so a later window cannot
 * clobber an earlier one's values.
 */
export function binsByLabel(response: JobSkillScores): Map<string, SkillBin> {
	const map = new Map<string, SkillBin>();
	for (const window of response.windows) {
		for (const bin of window.bins) {
			map.set(bin.label, bin);
		}
	}
	return map;
}

/** Values for one model across the shared lead axis; null where unscored. */
export function seriesValues(
	response: JobSkillScores,
	leads: LeadBin[],
	metric: SkillMetricKey
): (number | null)[] {
	const bins = binsByLabel(response);
	return leads.map((lead) => bins.get(lead.label)?.[metric] ?? null);
}

/** True when a response carries at least one usable value for the metric. */
export function hasMetric(response: JobSkillScores, metric: SkillMetricKey): boolean {
	return response.windows.some((window) => window.bins.some((bin) => bin[metric] != null));
}

/**
 * Skill scores are conventionally plain decimals — 0.82, not 82.0%.
 *
 * romp.yaml declares the Area Under ROC Curve's unit as `fraction`, which the shared
 * formatMetricValue renders as a percentage. That is right for false alarm rate
 * and miss rate but wrong here, so probabilistic scores format through this.
 */
export function formatSkillValue(value: number | null | undefined): string {
	if (value == null || Number.isNaN(value)) return '—';
	return value.toFixed(3);
}

/** Overall-score rows, in the order romp.yaml declares them. */
export const OVERALL_METRIC_ORDER = [
	'brier_score',
	'brier_skill_score',
	'ranked_probability_score',
	'ranked_probability_skill_score',
	'auc',
	'auc_ref'
] as const;

/**
 * Fallback labels for ids romp.yaml doesn't define (only auc_ref today).
 *
 * Metric names are always spelled out in the UI — never abbreviated to BSS,
 * AUC, FAR and so on. romp.yaml carries an `abbreviation` field; do not use it
 * for display.
 */
export const EXTRA_METRIC_LABELS: Record<string, string> = {
	auc_ref: 'Area Under ROC Curve (Traditional Climatology)'
};
