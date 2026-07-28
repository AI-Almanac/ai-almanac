import { describe, it, expect } from 'vitest';

import type { JobSkillScores, SkillBin } from '../src/lib/api';
import {
	binsByLabel,
	collectLeadBins,
	formatSkillValue,
	hasMetric,
	seriesValues
} from '../src/lib/skill-series';

function bin(label: string, min: number, max: number, overrides: Partial<SkillBin> = {}): SkillBin {
	return {
		bin: `Days ${label}`,
		label,
		lead_day_min: min,
		lead_day_max: max,
		brier_skill_score: 0.2,
		auc: 0.8,
		auc_ref: 0.5,
		brier_score_forecast: 0.1,
		brier_score_climatology: 0.14,
		...overrides
	};
}

/** A probabilistic job as ROMP emits it: two windows, three bins each. */
function response(jobId: string, overrides: Partial<SkillBin>[] = []): JobSkillScores {
	return {
		job_id: jobId,
		windows: [
			{
				model: 'fuxi',
				window: '1-15',
				overall: { brier_skill_score: 0.21, auc: 0.82 },
				bins: [
					bin('1-5', 1, 5, overrides[0]),
					bin('6-10', 6, 10, overrides[1]),
					bin('11-15', 11, 15, overrides[2])
				]
			},
			{
				model: 'fuxi',
				window: '16-30',
				overall: { brier_skill_score: 0.05, auc: 0.71 },
				bins: [
					bin('16-20', 16, 20, overrides[3]),
					bin('21-25', 21, 25, overrides[4]),
					bin('26-30', 26, 30, overrides[5])
				]
			}
		]
	};
}

describe('collectLeadBins', () => {
	it('composes both verification windows onto one 1-30 lead axis', () => {
		const leads = collectLeadBins([response('a')]);
		expect(leads.map((l) => l.label)).toEqual(['1-5', '6-10', '11-15', '16-20', '21-25', '26-30']);
		// Midpoints, so bins sit at the centre of their span.
		expect(leads.map((l) => l.day)).toEqual([3, 8, 13, 18, 23, 28]);
	});

	it('deduplicates bins shared across jobs', () => {
		const leads = collectLeadBins([response('a'), response('b')]);
		expect(leads).toHaveLength(6);
	});

	it('orders by lead day rather than insertion or string order', () => {
		const scrambled: JobSkillScores = {
			job_id: 'a',
			windows: [
				{
					model: 'm',
					window: '1-30',
					overall: {},
					bins: [bin('11-15', 11, 15), bin('1-5', 1, 5)]
				}
			]
		};
		expect(collectLeadBins([scrambled]).map((l) => l.label)).toEqual(['1-5', '11-15']);
	});

	it('returns nothing for a deterministic job', () => {
		expect(collectLeadBins([{ job_id: 'a', windows: [] }])).toEqual([]);
	});
});

describe('seriesValues', () => {
	it('aligns a model to the shared lead axis', () => {
		const res = response('a');
		const leads = collectLeadBins([res]);
		expect(seriesValues(res, leads, 'auc')).toEqual([0.8, 0.8, 0.8, 0.8, 0.8, 0.8]);
	});

	it('emits null for bins the model has no value for', () => {
		// Third bin unscored, as ROMP writes when a bin had no valid samples.
		const res = response('a', [{}, {}, { brier_skill_score: null }]);
		const leads = collectLeadBins([res]);
		expect(seriesValues(res, leads, 'brier_skill_score')[2]).toBeNull();
	});

	it('emits null where a model is missing a bin another job supplies', () => {
		const full = response('a');
		const partial: JobSkillScores = {
			job_id: 'b',
			windows: [{ model: 'm', window: '1-15', overall: {}, bins: [bin('1-5', 1, 5)] }]
		};
		const leads = collectLeadBins([full, partial]);
		expect(seriesValues(partial, leads, 'auc')).toEqual([0.8, null, null, null, null, null]);
	});

	it('preserves negative skill rather than clamping it', () => {
		const res = response('a', [{ brier_skill_score: -0.42 }]);
		const leads = collectLeadBins([res]);
		expect(seriesValues(res, leads, 'brier_skill_score')[0]).toBe(-0.42);
	});
});

describe('binsByLabel', () => {
	it('flattens both windows into one lookup', () => {
		expect(binsByLabel(response('a')).size).toBe(6);
	});
});

describe('hasMetric', () => {
	it('is false when every bin is unscored', () => {
		const empty = response('a', Array(6).fill({ auc: null }));
		expect(hasMetric(empty, 'auc')).toBe(false);
		expect(hasMetric(empty, 'brier_skill_score')).toBe(true);
	});

	it('is false for a deterministic job', () => {
		expect(hasMetric({ job_id: 'a', windows: [] }, 'auc')).toBe(false);
	});
});

describe('formatSkillValue', () => {
	it('renders AUC as a decimal, not a percentage', () => {
		// romp.yaml types auc as `fraction`, which the shared formatMetricValue
		// would render as "82.0%"; skill scores are conventionally plain decimals.
		expect(formatSkillValue(0.82)).toBe('0.820');
	});

	it('renders negative skill', () => {
		expect(formatSkillValue(-0.42)).toBe('-0.420');
	});

	it('renders missing values as an em dash', () => {
		expect(formatSkillValue(null)).toBe('—');
		expect(formatSkillValue(undefined)).toBe('—');
		expect(formatSkillValue(Number.NaN)).toBe('—');
	});
});
