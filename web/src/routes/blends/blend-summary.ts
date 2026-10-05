// Parses the blend's pooled per-model summary CSV into the skill series the
// lead-time chart and the overall-metrics strip render. Only the small pooled
// summary is read client-side; the large combined pickle is never loaded into
// the browser.
//
// Mirrored server-side by blend_domain._parse_pooled_summary, which feeds the
// same fields to the chat tool. Keep the two in sync.

export const SKILL_AXES = ['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Later'];
const AUC_COLUMNS = ['auc_week1', 'auc_week2', 'auc_week3', 'auc_week4', 'auc_later'];
const BRIER_COLUMNS = ['brier_week1', 'brier_week2', 'brier_week3', 'brier_week4', 'brier_later'];
const BLEND_MODEL = 'blended_model';

/**
 * The blend package scores every skill column against this model
 * (`summarize_models_pooled`, baseline_model). Per-lead Brier Skill Score is
 * derived from its `brier_week*` values.
 *
 * `unc` is *unconditional*, not uncalibrated: the spec builds it from
 * `prob_clim_mr_unc`, the climatology that does not condition on onset having
 * held off until the issue date. Nothing about it is Platt-scaled — only
 * forecast models carry that flag. Because that prefix reads as "uncalibrated",
 * the product name for it is Traditional Climatology.
 */
const BASELINE_MODEL = 'unc_clim_raw';

/**
 * The climatology that does condition on onset having held off until the issue
 * date, named Conditional Climatology in the UI.
 */
const CONDITIONAL_CLIMATOLOGY_MODEL = 'clim_raw';

export type SkillRow = {
	model: string;
	label: string;
	isBlend: boolean;
	isBaseline: boolean;
	auc: number;
	brier: number | null;
	rps: number | null;
	brierSkill: number | null;
	rpsSkill: number | null;
	/** KS statistic between the positive and negative score distributions. */
	pietra: number | null;
	observations: number | null;
	aucByLead: number[];
	brierByLead: (number | null)[];
	/** Derived: 1 - brier/baseline_brier per lead. Null where not derivable. */
	brierSkillByLead: (number | null)[];
};

/** A metric with a value per forecast lead, plottable as a curve. */
export type LeadMetric = {
	key: 'brierSkillByLead' | 'aucByLead';
	label: string;
	/** The value at which the metric indicates no skill. */
	reference: number;
	referenceLabel: string;
	caption: string;
};

/**
 * Metric names are spelled out in full — house convention. Brier Skill Score
 * leads because it is a skill score: zero is Traditional Climatology and
 * negative means worse, which is the honest framing. Area Under ROC Curve is offered second
 * because it compresses the differences between these models almost to nothing.
 */
export const LEAD_METRICS: LeadMetric[] = [
	{
		key: 'brierSkillByLead',
		label: 'Brier Skill Score',
		reference: 0,
		referenceLabel: 'No skill (Traditional Climatology)',
		caption:
			'Probability accuracy by forecast lead, measured against Traditional Climatology. 0 matches it, 1 is perfect, below 0 is worse — higher is better. Click a model to toggle it; hover for exact values.'
	},
	{
		key: 'aucByLead',
		label: 'Area Under ROC Curve',
		reference: 0.5,
		referenceLabel: 'No skill (chance)',
		caption:
			'Ability to rank onset weeks correctly, by forecast lead. 0.5 is no skill, 1.0 is perfect — higher is better. Click a model to toggle it; hover for exact values.'
	}
];

/** A metric the blend reports pooled over all leads, so it has no curve. */
export type OverallMetric = {
	key: 'rpsSkill' | 'brierSkill' | 'auc' | 'observations';
	label: string;
	/**
	 * The romp.yaml metric id, passed to skillAgainstReference so the strip
	 * shades on the same scale the benchmarks metric portrait uses. Null for
	 * values that are not a skill statement, e.g. a sample count.
	 */
	skillMetric: string | null;
	hint: string;
};

/**
 * Ranked Probability Skill Score leads: the outcome is five ordinal bins
 * (weeks 1-4, later), so a metric that credits being close beats one that only
 * asks whether the right bin won. It is also where the blend's advantage over
 * Traditional Climatology actually shows up.
 */
export const OVERALL_METRICS: OverallMetric[] = [
	{
		key: 'rpsSkill',
		label: 'Ranked Probability Skill Score',
		skillMetric: 'ranked_probability_skill_score',
		hint: 'Credits forecasts that land near the observed onset week, not just on it. Measured against Traditional Climatology.'
	},
	{
		key: 'brierSkill',
		label: 'Brier Skill Score',
		skillMetric: 'brier_skill_score',
		hint: 'Probability accuracy pooled over all leads, measured against Traditional Climatology.'
	},
	{
		key: 'auc',
		label: 'Area Under ROC Curve',
		skillMetric: 'auc',
		hint: 'Ability to rank onset weeks correctly. 0.5 is chance.'
	},
	{
		key: 'observations',
		label: 'Observations',
		skillMetric: null,
		hint: 'Scored grid-point years behind every number in this column.'
	}
];

// Turn a raw model id into a reader-friendly label.
//   blended_model                 -> "Blend"
//   clim_raw                      -> "Conditional Climatology"
//   unc_clim_raw                  -> "Traditional Climatology"
//   aifs_fixed_cutoff_raw         -> "AIFS (raw)"
//   aifs_calibrated_fixed_cutoff  -> "AIFS (calibrated)"
// (blends trained before the haiyang pin say _clim_mok_date instead)
//
// "calibrated" rather than "bias corrected": the step is Platt scaling of the
// probabilities so they match observed frequencies. It leaves the underlying
// rainfall biases untouched, so calling it bias correction overclaims — and the
// package's own column name is already `_calibrated`.
function prettyModel(model: string): string {
	if (model === BLEND_MODEL) return 'Blend';
	if (model === CONDITIONAL_CLIMATOLOGY_MODEL) return 'Conditional Climatology';
	if (model === BASELINE_MODEL) return 'Traditional Climatology';

	const calibrated = /calibrated/.test(model);
	const name = model
		.replace(/_calibrated/g, '')
		.replace(/_(fixed_cutoff|clim_mok_date)/g, '')
		.replace(/_raw$/, '')
		.replace(/_/g, ' ')
		.trim()
		.toUpperCase();
	return `${name} (${calibrated ? 'calibrated' : 'raw'})`;
}

/**
 * Series the lead-time chart shows before the reader touches the toggles.
 *
 * Raw forecast series score around -1 against Traditional Climatology, an order
 * of magnitude below the blend and the two climatologies, so including them by
 * default compresses the y range where the interesting differences live. They
 * stay one click away.
 */
export function isDefaultVisibleSeries(model: string): boolean {
	return (
		model === BLEND_MODEL || model === CONDITIONAL_CLIMATOLOGY_MODEL || model === BASELINE_MODEL
	);
}

function finite(value: string | undefined): number | null {
	const n = Number(value);
	// An empty cell coerces to 0, so reject blanks before the finite check —
	// pandas writes NaN as an empty string.
	if (value == null || value.trim() === '') return null;
	return Number.isFinite(n) ? n : null;
}

/**
 * Skill of a value against the baseline, in the standard lower-is-better form.
 *
 * A zero or missing baseline makes the ratio meaningless, so it yields null
 * rather than Infinity.
 */
function brierSkill(value: number | null, baseline: number | null): number | null {
	if (value == null || baseline == null || baseline === 0) return null;
	return 1 - value / baseline;
}

export function parsePooledSummary(csv: string): SkillRow[] {
	const lines = csv.trim().split(/\r?\n/);
	if (lines.length < 2) return [];
	const header = lines[0].split(',');
	const index = (name: string) => header.indexOf(name);
	const modelIdx = index('model');
	const leadIdx = AUC_COLUMNS.map(index);
	const brierLeadIdx = BRIER_COLUMNS.map(index);
	if (modelIdx < 0 || leadIdx.some((i) => i < 0)) return [];

	const at = (cells: string[], name: string) => {
		const i = index(name);
		return i < 0 ? null : finite(cells[i]);
	};

	const rows: SkillRow[] = [];
	for (const line of lines.slice(1)) {
		const cells = line.split(',');
		const model = cells[modelIdx];
		if (!model) continue;
		const aucByLead = leadIdx.map((i) => finite(cells[i]));
		if (aucByLead.some((v) => v === null)) continue;
		rows.push({
			model,
			label: prettyModel(model),
			isBlend: model === BLEND_MODEL,
			isBaseline: model === BASELINE_MODEL,
			auc: at(cells, 'auc') ?? 0,
			brier: at(cells, 'brier'),
			rps: at(cells, 'rps'),
			brierSkill: at(cells, 'brier_skill'),
			rpsSkill: at(cells, 'rps_skill'),
			pietra: at(cells, 'pietra'),
			observations: at(cells, 'n'),
			aucByLead: aucByLead as number[],
			brierByLead: brierLeadIdx.map((i) => (i < 0 ? null : finite(cells[i]))),
			// Filled in below, once the baseline row is known.
			brierSkillByLead: SKILL_AXES.map(() => null)
		});
	}

	// The blend reports raw Brier per lead but skill only pooled, so derive the
	// per-lead skill from the baseline row that is already in this same file.
	const baseline = rows.find((row) => row.isBaseline);
	if (baseline) {
		for (const row of rows) {
			row.brierSkillByLead = row.brierByLead.map((value, lead) =>
				brierSkill(value, baseline.brierByLead[lead])
			);
		}
	}

	// Blend first: it draws on top and leads the legend.
	return rows.sort((a, b) => Number(b.isBlend) - Number(a.isBlend));
}
