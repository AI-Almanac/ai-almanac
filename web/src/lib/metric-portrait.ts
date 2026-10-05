/**
 * The metrics × models portrait: every metric against every model, at every
 * lead-time window, at once.
 *
 * The point of this view is that cross-metric disagreement becomes geometric
 * rather than announced — if the best cell in the Brier Skill Score row is a
 * different column from the best cell in the Area Under ROC Curve row, you see
 * it without being told. That only works if the shading is comparable, so the
 * rules here are strict:
 *
 * - **Normalize within a (row, window) cell group.** Never across rows: each
 *   metric has its own unit and direction (`lower_is_better` in romp.yaml).
 *   And never across windows: skill degrades with lead time, so pooling both
 *   windows would make the shading mostly encode lead time and drown out the
 *   model-vs-model difference, which is the comparison the view exists for.
 * - Climatology is a pinned reference, per window, never ranked as a competitor.
 * - There is deliberately no aggregate score or overall-rank row. A single
 *   summary number is the thing this view exists to avoid.
 *
 * Kept out of the component so it can be unit tested.
 */
import type { JobMetrics, JobSkillScores, MetricDefinition } from '$lib/api';
import { lowerIsBetter } from '$lib/metric-metadata';

/** Per-year MAE variables are a series, not a portrait row. */
const ANNUAL_MAE_RE = /^mae_\d{4}$/;

/** Probabilistic rows, in romp.yaml declaration order. */
const PROBABILISTIC_ROWS = [
	'brier_skill_score',
	'ranked_probability_skill_score',
	'auc',
	'brier_score',
	'ranked_probability_score'
] as const;

/**
 * Skill scores are defined as zero-at-no-skill, so their reference needs no
 * lookup. Everything else resolves from the payload or has no reference.
 */
const IMPLICIT_REFERENCE: Record<string, number> = {
	brier_skill_score: 0,
	ranked_probability_skill_score: 0
};

export type PortraitCell = {
	/** Column identity — the job id, since models are one-per-job here. */
	key: string;
	model: string;
	window: string;
	value: number | null;
	/**
	 * Skill relative to this row's climatology reference, on a common scale where
	 * 0 means "matches climatology" and 1 means "perfect". Negative is worse than
	 * climatology. This is the only quantity the cell shading encodes.
	 *
	 * Null when the row has no reference on disk (Ranked Probability Score), in
	 * which case the cell is deliberately left unshaded rather than guessed at.
	 */
	skill: number | null;
	/** 1-based within this row and window; ties share a rank. Null when unranked. */
	rank: number | null;
	isBest: boolean;
	/** Worse than the climatology reference for this row and window. */
	worseThanReference: boolean;
};

export type PortraitRow = {
	metric: string;
	label: string;
	group: 'spatial' | 'probabilistic';
	unit: string | null;
	lowerIsBetter: boolean | null;
	/** Keyed by window, each in `columns` order. */
	cellsByWindow: Record<string, PortraitCell[]>;
	/** Climatology reference per window. */
	referenceByWindow: Record<string, number | null>;
	/** Windows in which this metric contradicts the consensus ordering. */
	disagreeingWindows: string[];
	disagrees: boolean;
};

export type PortraitColumn = {
	key: string;
	model: string;
	label: string;
};

export type Portrait = {
	windows: string[];
	columns: PortraitColumn[];
	rows: PortraitRow[];
	/** Metrics the suite does not compute, surfaced so absence stays visible. */
	notComputed: string[];
};

export type PortraitInput = {
	windows: string[];
	models: { key: string; model: string; label: string }[];
	metricsByJob: Record<string, JobMetrics | undefined>;
	skillByJob: Record<string, JobSkillScores | undefined>;
	definitions: MetricDefinition[] | Map<string, MetricDefinition>;
};

/**
 * Rank values best-first, honoring direction. Ties share a rank; nulls are
 * unranked rather than sorted to the end, so a missing value never reads as
 * "worst".
 */
export function rankValues(values: (number | null)[], lower: boolean | null): (number | null)[] {
	const present = values
		.map((value, index) => ({ value, index }))
		.filter((entry): entry is { value: number; index: number } => entry.value != null);
	if (present.length === 0) return values.map(() => null);
	// Direction unknown (romp.yaml leaves it null for e.g. bias) — don't guess.
	if (lower == null) return values.map(() => null);

	const sorted = [...present].sort((a, b) => (lower ? a.value - b.value : b.value - a.value));
	const ranks: (number | null)[] = values.map(() => null);
	let rank = 0;
	let previous: number | null = null;
	sorted.forEach((entry, offset) => {
		if (previous === null || entry.value !== previous) rank = offset + 1;
		previous = entry.value;
		ranks[entry.index] = rank;
	});
	return ranks;
}

/** Metrics whose value is already a skill score against climatology. */
const IS_SKILL_SCORE = new Set(['brier_skill_score', 'ranked_probability_skill_score']);

/**
 * Express a value as skill relative to its reference, on one common scale:
 * `0` matches the reference, `1` is perfect, negative is worse than the
 * reference. This is the single quantity the portrait's shading encodes.
 *
 * Why this rather than rank-within-row: climatology is a meaningful zero, so
 * distance from it is an absolute statement that holds for a single model. Rank
 * is relative and says nothing when only one model ran — which is the common
 * case — and using color for both made the two indistinguishable.
 *
 * Returns null when the row has no reference, so the cell stays unshaded rather
 * than being assigned a severity it cannot support.
 */
export function skillAgainstReference(
	metric: string,
	value: number | null,
	reference: number | null,
	lower: boolean | null
): number | null {
	if (value == null) return null;
	// Already a skill score by construction; its reference is zero.
	if (IS_SKILL_SCORE.has(metric)) return value;
	if (reference == null || lower == null) return null;

	if (lower) {
		// Brier, Miss Rate, MAE, ... — the standard skill-score form.
		// A zero reference would make the ratio meaningless.
		if (reference === 0) return null;
		return 1 - value / reference;
	}

	// Higher-is-better with a floor at chance rather than at zero. Measuring the
	// Area Under ROC Curve's shortfall against how far climatology itself sits
	// above chance keeps the result on the same scale as the skill scores; using
	// (value - reference) / (1 - reference) instead pushes a modest shortfall past
	// -1 and saturates the ramp immediately.
	const CHANCE = metric === 'auc' ? 0.5 : 0;
	const headroom = reference - CHANCE;
	if (headroom <= 0) return null;
	return (value - reference) / headroom;
}

/**
 * A row's strict preference between two columns within one window: -1 if `a` is
 * better, 1 if `b` is, 0 when tied or either is unranked.
 *
 * A tie is genuinely "no opinion" and must never count as disagreement — a row
 * where two models score identically contradicts nothing.
 */
function preference(row: PortraitRow, window: string, a: string, b: string): number {
	const cells = row.cellsByWindow[window] ?? [];
	const ra = cells.find((cell) => cell.key === a)?.rank;
	const rb = cells.find((cell) => cell.key === b)?.rank;
	if (ra == null || rb == null || ra === rb) return 0;
	return ra < rb ? -1 : 1;
}

/**
 * Flag metrics that contradict the consensus ordering of the models.
 *
 * Computed **per window**, because models can legitimately agree at short range
 * and disagree at extended range — pooling the windows would hide exactly the
 * lead-time-dependent disagreement that's most worth seeing.
 *
 * Comparison is pairwise rather than whole-ordering: for each pair of models the
 * consensus is whichever direction most metrics prefer, and a row is flagged if
 * it strictly reverses at least one pair. Pairs where metrics split evenly have
 * no consensus and are skipped, so nothing is flagged for deviating from an
 * ordering that was arbitrary to begin with.
 *
 * Mutates `disagreeingWindows` / `disagrees` in place. Deliberately not an
 * aggregate score — it points at rows worth a second look and says nothing about
 * which model is better.
 */
export function markDisagreements(
	rows: PortraitRow[],
	windows: string[],
	columns: PortraitColumn[]
): PortraitRow[] {
	for (const row of rows) {
		row.disagreeingWindows = [];
		row.disagrees = false;
	}
	// Ranking is meaningless with one model; there is no majority with one metric.
	if (columns.length < 2 || rows.length < 2) return rows;

	for (const window of windows) {
		const flagged = new Set<PortraitRow>();
		for (let i = 0; i < columns.length; i++) {
			for (let j = i + 1; j < columns.length; j++) {
				const [a, b] = [columns[i].key, columns[j].key];
				const votes = rows.map((row) => preference(row, window, a, b));
				const forA = votes.filter((vote) => vote === -1).length;
				const forB = votes.filter((vote) => vote === 1).length;
				if (forA === forB) continue; // no consensus on this pair
				const consensus = forA > forB ? -1 : 1;
				rows.forEach((row, index) => {
					if (votes[index] !== 0 && votes[index] !== consensus) flagged.add(row);
				});
			}
		}
		for (const row of flagged) row.disagreeingWindows.push(window);
	}

	for (const row of rows) row.disagrees = row.disagreeingWindows.length > 0;
	return rows;
}

/** Region-mean of a spatial metric for one model and window. */
function spatialValue(
	metrics: JobMetrics | undefined,
	window: string,
	metric: string,
	model?: string
): number | null {
	const match = metrics?.windows.find(
		(w) => w.window === window && (model ? w.model === model : w.model !== 'climatology')
	);
	return match?.metrics[metric]?.mean ?? null;
}

/** Probabilistic overall score for one model and window. */
function overallValue(
	skill: JobSkillScores | undefined,
	window: string,
	metric: string
): number | null {
	const match = skill?.windows.find((w) => w.window === window);
	return match?.overall[metric] ?? null;
}

/**
 * The climatology reference for a probabilistic row.
 *
 * ROMP's overall CSV carries only AUC_ref, so the Brier reference is recovered
 * by averaging the per-bin climatology column. Ranked Probability Score has no
 * reference on disk.
 */
function probabilisticReference(
	skill: JobSkillScores | undefined,
	window: string,
	metric: string
): number | null {
	if (metric in IMPLICIT_REFERENCE) return IMPLICIT_REFERENCE[metric];
	const match = skill?.windows.find((w) => w.window === window);
	if (!match) return null;
	if (metric === 'auc') return match.overall.auc_ref ?? null;
	if (metric === 'brier_score') {
		const values = match.bins
			.map((bin) => bin.brier_score_climatology)
			.filter((value): value is number => value != null);
		if (values.length === 0) return null;
		return values.reduce((sum, value) => sum + value, 0) / values.length;
	}
	return null;
}

function buildRow(
	metric: string,
	group: 'spatial' | 'probabilistic',
	valuesByWindow: Record<string, (number | null)[]>,
	referenceByWindow: Record<string, number | null>,
	input: PortraitInput
): PortraitRow {
	const definition =
		input.definitions instanceof Map
			? input.definitions.get(metric)
			: input.definitions.find((d) => d.id === metric);
	const lower = lowerIsBetter(metric, input.definitions);

	const cellsByWindow: Record<string, PortraitCell[]> = {};
	for (const window of input.windows) {
		const values = valuesByWindow[window] ?? input.models.map(() => null);
		// Ranked within this window only. Rank no longer drives color — it only
		// marks the leader in bold — but it is still what disagreement detection
		// compares across rows.
		const ranks = rankValues(values, lower);
		const reference = referenceByWindow[window] ?? null;
		// With one model, or an all-tied group, "best" is not a claim worth making.
		const comparable = new Set(values.filter((v): v is number => v != null)).size >= 2;
		cellsByWindow[window] = input.models.map((model, index) => ({
			key: model.key,
			model: model.label,
			window,
			value: values[index],
			skill: skillAgainstReference(metric, values[index], reference, lower),
			rank: ranks[index],
			isBest: comparable && ranks[index] === 1,
			worseThanReference:
				reference != null && values[index] != null && lower != null
					? lower
						? (values[index] as number) > reference
						: (values[index] as number) < reference
					: false
		}));
	}

	return {
		metric,
		label: definition?.label ?? metric,
		group,
		unit: definition?.unit ?? null,
		lowerIsBetter: lower,
		cellsByWindow,
		referenceByWindow,
		disagreeingWindows: [],
		disagrees: false
	};
}

export function buildPortrait(input: PortraitInput): Portrait {
	const { windows, models, metricsByJob, skillByJob } = input;

	// Spatial rows: whatever metrics appear in the NetCDF payloads, minus the
	// per-year MAE series.
	const spatialMetrics = [
		...new Set(
			models.flatMap((model) =>
				(metricsByJob[model.key]?.windows ?? [])
					.filter((w) => windows.includes(w.window) && w.model !== 'climatology')
					.flatMap((w) => Object.keys(w.metrics))
			)
		)
	].filter((metric) => !ANNUAL_MAE_RE.test(metric));

	const rows: PortraitRow[] = [];

	for (const metric of spatialMetrics) {
		const valuesByWindow: Record<string, (number | null)[]> = {};
		const referenceByWindow: Record<string, number | null> = {};
		for (const window of windows) {
			valuesByWindow[window] = models.map((model) =>
				spatialValue(metricsByJob[model.key], window, metric)
			);
			referenceByWindow[window] =
				models
					.map((model) => spatialValue(metricsByJob[model.key], window, metric, 'climatology'))
					.find((value) => value != null) ?? null;
		}
		if (Object.values(valuesByWindow).some((values) => values.some((v) => v != null))) {
			rows.push(buildRow(metric, 'spatial', valuesByWindow, referenceByWindow, input));
		}
	}

	for (const metric of PROBABILISTIC_ROWS) {
		const valuesByWindow: Record<string, (number | null)[]> = {};
		const referenceByWindow: Record<string, number | null> = {};
		for (const window of windows) {
			valuesByWindow[window] = models.map((model) =>
				overallValue(skillByJob[model.key], window, metric)
			);
			referenceByWindow[window] =
				models
					.map((model) => probabilisticReference(skillByJob[model.key], window, metric))
					.find((value) => value != null) ?? null;
		}
		if (!Object.values(valuesByWindow).some((values) => values.some((v) => v != null))) continue;
		rows.push(buildRow(metric, 'probabilistic', valuesByWindow, referenceByWindow, input));
	}

	const columns = models.map((model) => ({ ...model }));
	markDisagreements(rows, windows, columns);

	return {
		windows,
		columns,
		rows,
		// Named explicitly so a user can see that calibration and ensemble
		// dispersion are unmeasured rather than assuming discrimination covers them.
		notComputed: [
			'Reliability',
			'Continuous Ranked Probability Score',
			'Ensemble spread–skill ratio',
			'Rank histogram'
		]
	};
}

/**
 * Windows present across both payload families, ordered by lead day.
 *
 * `keys` scopes the search to the run set currently on screen. The caches are
 * module-level and accumulate across group switches without ever being pruned,
 * so iterating all of their values would carry a previously-viewed run set's
 * windows into this one and manufacture entire columns of em-dashes.
 */
export function portraitWindows(
	metricsByJob: Record<string, JobMetrics | undefined>,
	skillByJob: Record<string, JobSkillScores | undefined>,
	keys: string[]
): string[] {
	const windows = new Set<string>();
	for (const key of keys) {
		for (const w of metricsByJob[key]?.windows ?? []) {
			if (w.model !== 'climatology') windows.add(w.window);
		}
		for (const w of skillByJob[key]?.windows ?? []) windows.add(w.window);
	}
	return [...windows].sort((a, b) => leadStart(a) - leadStart(b) || a.localeCompare(b));
}

function leadStart(window: string): number {
	const parsed = Number.parseInt(window.split('-')[0], 10);
	return Number.isNaN(parsed) ? Number.MAX_SAFE_INTEGER : parsed;
}
