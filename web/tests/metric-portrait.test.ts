import { describe, it, expect } from 'vitest';

import type { JobMetrics, JobSkillScores, MetricDefinition } from '../src/lib/api';
import {
	buildPortrait,
	markDisagreements,
	portraitWindows,
	rankValues,
	skillAgainstReference,
	type PortraitColumn,
	type PortraitRow
} from '../src/lib/metric-portrait';

const WINDOWS = ['1-15', '16-30'];

const DEFINITIONS = [
	{ id: 'false_alarm_rate', label: 'False Alarm Rate', unit: 'fraction', lower_is_better: true },
	{ id: 'mean_mae', label: 'Mean Absolute Error', unit: 'days', lower_is_better: true },
	{ id: 'brier_score', label: 'Brier Score', unit: 'dimensionless', lower_is_better: true },
	{
		id: 'brier_skill_score',
		label: 'Brier Skill Score',
		unit: 'dimensionless',
		lower_is_better: false
	},
	{ id: 'auc', label: 'Area Under ROC Curve', unit: 'fraction', lower_is_better: false },
	{ id: 'bias', label: 'Bias', unit: 'mm', lower_is_better: null }
] as MetricDefinition[];

describe('rankValues', () => {
	it('ranks lower-is-better ascending and higher-is-better descending', () => {
		expect(rankValues([0.3, 0.1, 0.2], true)).toEqual([3, 1, 2]);
		expect(rankValues([0.3, 0.1, 0.2], false)).toEqual([1, 3, 2]);
	});

	it('shares a rank on ties', () => {
		expect(rankValues([0.2, 0.2, 0.5], false)).toEqual([2, 2, 1]);
	});

	it('leaves missing values unranked rather than ranking them worst', () => {
		expect(rankValues([0.3, null, 0.1], true)).toEqual([2, null, 1]);
	});

	it('refuses to rank when direction is unknown', () => {
		// romp.yaml leaves lower_is_better null for bias; guessing would mislead.
		expect(rankValues([0.3, 0.1], null)).toEqual([null, null]);
	});
});

describe('skillAgainstReference', () => {
	it('passes skill scores through, since they already are the quantity', () => {
		expect(skillAgainstReference('brier_skill_score', -0.398, 0, false)).toBe(-0.398);
		expect(skillAgainstReference('ranked_probability_skill_score', 0.21, 0, false)).toBe(0.21);
	});

	it('uses the standard skill-score form for lower-is-better metrics', () => {
		// Brier 0.117 against climatology 0.084 → 1 - 0.117/0.084.
		expect(skillAgainstReference('brier_score', 0.117, 0.084, true)).toBeCloseTo(-0.393, 3);
		// Matching the reference is exactly zero on the scale.
		expect(skillAgainstReference('brier_score', 0.084, 0.084, true)).toBe(0);
		// Beating it is positive.
		expect(skillAgainstReference('brier_score', 0.042, 0.084, true)).toBeCloseTo(0.5, 6);
	});

	it('measures ROC shortfall against climatology headroom above chance', () => {
		// (0.756 - 0.905) / (0.905 - 0.5). Dividing by (1 - reference) instead would
		// give -1.57 and saturate the ramp on a modest shortfall.
		expect(skillAgainstReference('auc', 0.756, 0.905, false)).toBeCloseTo(-0.368, 3);
		expect(skillAgainstReference('auc', 0.905, 0.905, false)).toBe(0);
		expect(skillAgainstReference('auc', 0.93, 0.905, false)).toBeCloseTo(0.062, 3);
	});

	it('returns null when no reference exists, rather than inventing severity', () => {
		// Ranked Probability Score has no reference on disk.
		expect(skillAgainstReference('ranked_probability_score', 0.538, null, true)).toBeNull();
	});

	it('returns null rather than dividing by a zero or degenerate reference', () => {
		expect(skillAgainstReference('brier_score', 0.1, 0, true)).toBeNull();
		// Climatology at or below chance leaves no headroom to measure against.
		expect(skillAgainstReference('auc', 0.7, 0.5, false)).toBeNull();
		expect(skillAgainstReference('auc', 0.7, 0.4, false)).toBeNull();
	});

	it('returns null for a missing value or unknown direction', () => {
		expect(skillAgainstReference('brier_score', null, 0.084, true)).toBeNull();
		expect(skillAgainstReference('bias', 0.3, 0.2, null)).toBeNull();
	});

	it('is signed consistently across both metric directions', () => {
		// Worse than climatology is negative whether the metric is lower- or
		// higher-is-better — that is the whole point of the common scale.
		expect(skillAgainstReference('brier_score', 0.2, 0.1, true)).toBeLessThan(0);
		expect(skillAgainstReference('auc', 0.6, 0.9, false)!).toBeLessThan(0);
		expect(skillAgainstReference('brier_score', 0.05, 0.1, true)!).toBeGreaterThan(0);
		expect(skillAgainstReference('auc', 0.95, 0.9, false)!).toBeGreaterThan(0);
	});
});

function columns(keys: string[]): PortraitColumn[] {
	return keys.map((key) => ({ key, model: key, label: key.toUpperCase() }));
}

/** A row whose ranks per window are given directly. */
function row(
	metric: string,
	ranksByWindow: Record<string, (number | null)[]>,
	keys = ['a', 'b', 'c']
): PortraitRow {
	const cellsByWindow: Record<string, PortraitRow['cellsByWindow'][string]> = {};
	for (const [window, ranks] of Object.entries(ranksByWindow)) {
		cellsByWindow[window] = keys.map((key, index) => ({
			key,
			model: key.toUpperCase(),
			window,
			value: ranks[index] == null ? null : 1 / (ranks[index] as number),
			skill: null,
			rank: ranks[index],
			isBest: ranks[index] === 1,
			worseThanReference: false
		}));
	}
	return {
		metric,
		label: metric,
		group: 'probabilistic',
		unit: null,
		lowerIsBetter: false,
		cellsByWindow,
		referenceByWindow: Object.fromEntries(Object.keys(ranksByWindow).map((w) => [w, 0])),
		disagreeingWindows: [],
		disagrees: false
	};
}

describe('markDisagreements', () => {
	const cols = columns(['a', 'b', 'c']);

	it('flags the row that orders models differently from the majority', () => {
		const rows = [
			row('m1', { '1-15': [1, 2, 3] }),
			row('m2', { '1-15': [1, 2, 3] }),
			row('m3', { '1-15': [2, 1, 3] })
		];
		markDisagreements(rows, ['1-15'], cols);
		expect(rows.map((r) => r.disagrees)).toEqual([false, false, true]);
	});

	it('reports disagreement per window, not pooled across them', () => {
		// Everyone agrees at short range; m3 dissents only at extended range.
		const rows = [
			row('m1', { '1-15': [1, 2, 3], '16-30': [1, 2, 3] }),
			row('m2', { '1-15': [1, 2, 3], '16-30': [1, 2, 3] }),
			row('m3', { '1-15': [1, 2, 3], '16-30': [2, 1, 3] })
		];
		markDisagreements(rows, WINDOWS, cols);
		expect(rows[2].disagreeingWindows).toEqual(['16-30']);
		expect(rows[0].disagreeingWindows).toEqual([]);
	});

	it('does not treat a fully tied row as a dissent', () => {
		// A row where every model scores identically expresses no ordering, so it
		// cannot contradict one.
		const rows = [
			row('m1', { '1-15': [1, 2, 3] }),
			row('m2', { '1-15': [1, 2, 3] }),
			row('tied', { '1-15': [1, 1, 1] })
		];
		markDisagreements(rows, ['1-15'], cols);
		expect(rows.every((r) => !r.disagrees)).toBe(true);
	});

	it('flags nothing on an even split, having no majority to deviate from', () => {
		const rows = [row('m1', { '1-15': [1, 2, 3] }), row('m2', { '1-15': [3, 2, 1] })];
		markDisagreements(rows, ['1-15'], cols);
		expect(rows.every((r) => !r.disagrees)).toBe(true);
	});

	it('flags nothing with a single model, where ranking is meaningless', () => {
		const rows = [row('m1', { '1-15': [1] }, ['a']), row('m2', { '1-15': [1] }, ['a'])];
		markDisagreements(rows, ['1-15'], columns(['a']));
		expect(rows.every((r) => !r.disagrees)).toBe(true);
	});

	it('ignores unrankable rows when finding the majority', () => {
		const rows = [
			row('m1', { '1-15': [1, 2, 3] }),
			row('m2', { '1-15': [1, 2, 3] }),
			row('bias', { '1-15': [null, null, null] }),
			row('m3', { '1-15': [3, 2, 1] })
		];
		markDisagreements(rows, ['1-15'], cols);
		expect(rows.map((r) => r.disagrees)).toEqual([false, false, false, true]);
	});
});

const stats = (mean: number) => ({
	mean,
	min: mean,
	max: mean,
	p25: mean,
	p50: mean,
	p75: mean,
	p90: mean,
	unit: 'x'
});

/** Spatial payload with a per-window value; longer lead is worse, as in reality. */
function metricsFor(model: string, short: number, long: number): JobMetrics {
	return {
		job_id: `job-${model}`,
		grid: null,
		bbox: null,
		windows: [
			{
				window: '1-15',
				model,
				tolerance_days: 3,
				metrics: { false_alarm_rate: stats(short), mae_2019: stats(1) }
			},
			{ window: '16-30', model, tolerance_days: 5, metrics: { false_alarm_rate: stats(long) } },
			{
				window: '1-15',
				model: 'climatology',
				tolerance_days: 3,
				metrics: { false_alarm_rate: stats(0.53) }
			},
			{
				window: '16-30',
				model: 'climatology',
				tolerance_days: 5,
				metrics: { false_alarm_rate: stats(0.58) }
			}
		]
	} as JobMetrics;
}

function skillFor(
	model: string,
	shortBss: number,
	shortAuc: number,
	longBss: number,
	longAuc: number
): JobSkillScores {
	const bin = (min: number, max: number, bss: number, auc: number, ref: number) => ({
		bin: `Days ${min}-${max}`,
		label: `${min}-${max}`,
		lead_day_min: min,
		lead_day_max: max,
		brier_skill_score: bss,
		auc,
		auc_ref: ref,
		brier_score_forecast: 0.1,
		brier_score_climatology: 0.14
	});
	// ROMP's overall CSV always carries Fair_RPS, so ranked_probability_score is
	// always present — and is the one row with no reference on disk. Omitting it
	// meant the row was never built and the no-reference assertions below silently
	// asserted against undefined.
	return {
		job_id: `job-${model}`,
		windows: [
			{
				model,
				window: '1-15',
				overall: {
					brier_skill_score: shortBss,
					auc: shortAuc,
					auc_ref: 0.51,
					brier_score: 0.1,
					ranked_probability_score: 0.09
				},
				bins: [bin(1, 5, shortBss, shortAuc, 0.51)]
			},
			{
				model,
				window: '16-30',
				overall: {
					brier_skill_score: longBss,
					auc: longAuc,
					auc_ref: 0.5,
					brier_score: 0.13,
					ranked_probability_score: 0.12
				},
				bins: [bin(16, 20, longBss, longAuc, 0.5)]
			}
		]
	};
}

describe('buildPortrait', () => {
	const models = [
		{ key: 'job-gc', model: 'graphcast', label: 'GraphCast' },
		{ key: 'job-ai', model: 'aifsens2', label: 'AIFSENS2' }
	];

	// GraphCast leads everything at 1-15. At 16-30 it still leads Brier Skill
	// Score but loses the ROC curve — the disagreement worth surfacing.
	const input = {
		windows: WINDOWS,
		models,
		metricsByJob: {
			'job-gc': metricsFor('graphcast', 0.09, 0.19),
			'job-ai': metricsFor('aifsens2', 0.14, 0.24)
		},
		skillByJob: {
			'job-gc': skillFor('graphcast', 0.24, 0.79, 0.11, 0.62),
			'job-ai': skillFor('aifsens2', 0.19, 0.72, 0.06, 0.7)
		},
		definitions: DEFINITIONS
	};

	it('carries a cell group for every window', () => {
		const portrait = buildPortrait(input);
		expect(portrait.windows).toEqual(WINDOWS);
		for (const row of portrait.rows) {
			expect(Object.keys(row.cellsByWindow)).toEqual(WINDOWS);
			for (const window of WINDOWS) {
				expect(row.cellsByWindow[window]).toHaveLength(2);
			}
		}
	});

	it('shades by distance from that window’s own climatology reference', () => {
		const portrait = buildPortrait(input);
		const far = portrait.rows.find((r) => r.metric === 'false_alarm_rate');
		// Both models beat climatology in both windows, so all four are positive —
		// but each is measured against its own window's reference (0.53 / 0.58),
		// not against the other window.
		expect(far?.cellsByWindow['1-15'][0].skill).toBeCloseTo(1 - 0.09 / 0.53, 6);
		expect(far?.cellsByWindow['16-30'][0].skill).toBeCloseTo(1 - 0.19 / 0.58, 6);
		for (const window of WINDOWS) {
			for (const cell of far?.cellsByWindow[window] ?? []) {
				expect(cell.skill).toBeGreaterThan(0);
			}
		}
	});

	it('grades severity rather than flattening everything below climatology', () => {
		// The real GenCast case: skill degrades with lead time, and the scale has to
		// show that rather than painting every sub-climatology cell identically.
		const worse = {
			...input,
			models: [models[0]],
			metricsByJob: {},
			skillByJob: { 'job-gc': skillFor('graphcast', -0.398, 0.756, -0.571, 0.665) }
		};
		const portrait = buildPortrait(worse);
		const bss = portrait.rows.find((r) => r.metric === 'brier_skill_score');
		const short = bss!.cellsByWindow['1-15'][0].skill!;
		const long = bss!.cellsByWindow['16-30'][0].skill!;
		expect(short).toBeLessThan(0);
		expect(long).toBeLessThan(short);
	});

	it('resolves a climatology reference per window', () => {
		const far = buildPortrait(input).rows.find((r) => r.metric === 'false_alarm_rate');
		expect(far?.referenceByWindow).toEqual({ '1-15': 0.53, '16-30': 0.58 });
	});

	it('uses zero as the skill-score reference in every window', () => {
		const bss = buildPortrait(input).rows.find((r) => r.metric === 'brier_skill_score');
		expect(bss?.referenceByWindow).toEqual({ '1-15': 0, '16-30': 0 });
	});

	it('reads the ROC reference from each window separately', () => {
		const auc = buildPortrait(input).rows.find((r) => r.metric === 'auc');
		expect(auc?.referenceByWindow['1-15']).toBe(0.51);
		expect(auc?.referenceByWindow['16-30']).toBe(0.5);
	});

	it('recovers the Brier reference by averaging the per-bin climatology column', () => {
		const brier = buildPortrait(input).rows.find((r) => r.metric === 'brier_score');
		expect(brier?.referenceByWindow['1-15']).toBeCloseTo(0.14);
	});

	it('flags the metric that dissents, and says which window it dissents in', () => {
		const portrait = buildPortrait(input);
		expect(portrait.rows.filter((r) => r.disagrees).map((r) => r.metric)).toEqual(['auc']);
		const auc = portrait.rows.find((r) => r.metric === 'auc');
		expect(auc?.disagreeingWindows).toEqual(['16-30']);
	});

	it('excludes the per-year MAE series', () => {
		expect(buildPortrait(input).rows.map((r) => r.metric)).not.toContain('mae_2019');
	});

	it('spells metric names out in full', () => {
		const labels = buildPortrait(input).rows.map((r) => r.label);
		expect(labels).toContain('Brier Skill Score');
		expect(labels).toContain('Area Under ROC Curve');
		expect(labels).not.toContain('BSS');
		expect(labels).not.toContain('AUC');
	});

	it('names the uncomputed metrics, spelled out', () => {
		const portrait = buildPortrait(input);
		expect(portrait.notComputed).toContain('Reliability');
		expect(portrait.notComputed).toContain('Continuous Ranked Probability Score');
		expect(portrait.notComputed).not.toContain('CRPS');
	});

	it('flags a model worse than climatology', () => {
		const worse = {
			...input,
			skillByJob: {
				'job-gc': skillFor('graphcast', -0.04, 0.45, -0.1, 0.4),
				'job-ai': skillFor('aifsens2', 0.19, 0.84, 0.06, 0.7)
			}
		};
		const bss = buildPortrait(worse).rows.find((r) => r.metric === 'brier_skill_score');
		expect(bss?.cellsByWindow['1-15'][0].worseThanReference).toBe(true);
		expect(bss?.cellsByWindow['1-15'][1].worseThanReference).toBe(false);
	});

	it('still shades a single-model run, but crowns no winner', () => {
		// The GenCast-only case. Distance from climatology is meaningful with one
		// model, so cells stay informative — unlike rank, which is not.
		const solo = {
			...input,
			models: [models[0]],
			metricsByJob: { 'job-gc': input.metricsByJob['job-gc'] },
			skillByJob: { 'job-gc': input.skillByJob['job-gc'] }
		};
		const portrait = buildPortrait(solo);
		for (const row of portrait.rows) {
			for (const window of WINDOWS) {
				for (const cell of row.cellsByWindow[window]) {
					// "Best of one" is not a claim worth making.
					expect(cell.isBest).toBe(false);
				}
			}
		}
		// Not optional-chained: a missing row must fail loudly rather than quietly
		// asserting against undefined.
		const bss = portrait.rows.find((r) => r.metric === 'brier_skill_score')!;
		expect(bss.cellsByWindow['1-15'][0].skill).not.toBeNull();

		// Ranked Probability Score has no reference on disk, so it carries no signal
		// at all — it must stay unshaded rather than look like a win.
		const rps = portrait.rows.find((r) => r.metric === 'ranked_probability_score')!;
		expect(rps).toBeDefined();
		expect(rps.referenceByWindow['1-15']).toBeNull();
		expect(rps.cellsByWindow['1-15'][0].skill).toBeNull();
		expect(rps.cellsByWindow['1-15'][0].worseThanReference).toBe(false);
	});

	it('handles a single window', () => {
		const portrait = buildPortrait({ ...input, windows: ['1-15'] });
		expect(portrait.windows).toEqual(['1-15']);
		expect(Object.keys(portrait.rows[0].cellsByWindow)).toEqual(['1-15']);
	});

	it('builds spatial-only rows for a deterministic run', () => {
		expect(
			buildPortrait({ ...input, skillByJob: {} }).rows.every((r) => r.group === 'spatial')
		).toBe(true);
	});

	it('builds probabilistic-only rows for an ensemble run with no spatial data', () => {
		expect(
			buildPortrait({ ...input, metricsByJob: {} }).rows.every((r) => r.group === 'probabilistic')
		).toBe(true);
	});
});

describe('portraitWindows', () => {
	const metrics = {
		a: {
			job_id: 'a',
			grid: null,
			bbox: null,
			windows: [
				{ window: '16-30', model: 'm', tolerance_days: 5, metrics: {} },
				{ window: '1-15', model: 'climatology', tolerance_days: 3, metrics: {} }
			]
		} as JobMetrics,
		stale: {
			job_id: 'stale',
			grid: null,
			bbox: null,
			windows: [{ window: 'all', model: 'm', tolerance_days: 3, metrics: {} }]
		} as JobMetrics
	};
	const skill = {
		b: { job_id: 'b', windows: [{ model: 'm', window: '1-15', overall: {}, bins: [] }] }
	};

	it('orders windows by lead day and merges both payload families', () => {
		// The climatology-only spatial window doesn't count; 1-15 arrives from the
		// skill payload instead.
		expect(portraitWindows(metrics, skill, ['a', 'b'])).toEqual(['1-15', '16-30']);
	});

	it('ignores cached jobs outside the current run set', () => {
		// The caches are module-level and never pruned, so a previously-viewed run
		// set would otherwise contribute an entire column group of em-dashes.
		expect(portraitWindows(metrics, skill, ['a', 'b'])).not.toContain('all');
		expect(portraitWindows(metrics, skill, ['stale'])).toEqual(['all']);
	});
});
