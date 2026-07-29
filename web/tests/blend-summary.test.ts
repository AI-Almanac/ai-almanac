import { describe, it, expect } from 'vitest';

import { parsePooledSummary } from '../src/routes/blends/blend-summary';

// Trimmed real header + two rows from a blend's summary_models_pooled CSV.
const HEADER =
	'id,brier,rps,auc,n,lat,lon,pietra,brier_week1,brier_week2,brier_week3,brier_week4,brier_later,auc_week1,auc_week2,auc_week3,auc_week4,auc_later,model,cv_method,brier_skill,rps_skill,AUC diff';
const BLEND =
	'ALL,0.576,0.479,0.835,26622,,,0.487,0.088,0.113,0.118,0.119,0.137,0.890,0.787,0.737,0.716,0.881,blended_model,global,0.036,0.129,0.62';
const AIFS =
	'ALL,1.036,0.993,0.676,26622,,,0.352,0.140,0.166,0.187,0.210,0.331,0.665,0.570,0.536,0.555,0.700,aifs_clim_mok_date_raw,raw,-0.73,-0.80,-15.3';
// The reference every skill score in the file is measured against. Its
// brier_week* values are what per-lead Brier Skill Score is derived from.
const BASELINE =
	'ALL,0.598,0.551,0.830,26622,,,0.485,0.091,0.114,0.117,0.120,0.156,0.886,0.783,0.735,0.714,0.878,unc_clim_raw,global,0,0,0';

describe('parsePooledSummary', () => {
	it('extracts AUC-by-lead series and labels the blend', () => {
		const rows = parsePooledSummary([HEADER, BLEND, AIFS].join('\n'));
		expect(rows).toHaveLength(2);
		// Blend is sorted first.
		expect(rows[0].isBlend).toBe(true);
		expect(rows[0].label).toBe('Blend');
		expect(rows[0].auc).toBeCloseTo(0.835);
		expect(rows[0].aucByLead).toEqual([0.89, 0.787, 0.737, 0.716, 0.881]);
		expect(rows[0].brierSkill).toBeCloseTo(0.036);
	});

	it('prettifies component model names without abbreviating', () => {
		const rows = parsePooledSummary([HEADER, AIFS].join('\n'));
		expect(rows[0].label).toBe('AIFS (raw)');
	});

	it('spells out climatology baselines', () => {
		const cal =
			'ALL,0.6,0.5,0.7,1,,,0.3,0,0,0,0,0,0.7,0.6,0.55,0.54,0.6,fuxi_calibrated_clim_mok_date,calibrated,0,0,0';
		const unc = 'ALL,0.6,0.5,0.7,1,,,0.3,0,0,0,0,0,0.7,0.6,0.55,0.54,0.6,unc_clim_raw,raw,0,0,0';
		const labels = parsePooledSummary([HEADER, cal, unc].join('\n')).map((r) => r.label);
		// "bias corrected" is the product term for the package's `_calibrated`
		// columns, and `unc_` is unconditional climatology — not uncalibrated.
		expect(labels).toContain('FUXI (bias corrected)');
		expect(labels).toContain('Climatology (unconditional)');
	});

	it('returns [] when the header lacks AUC columns', () => {
		expect(parsePooledSummary('id,model\nALL,blended_model')).toEqual([]);
	});

	it('returns [] for empty input', () => {
		expect(parsePooledSummary('')).toEqual([]);
	});

	it('keeps the pooled scores the chart cannot plot as a curve', () => {
		const [blend] = parsePooledSummary([HEADER, BLEND, BASELINE].join('\n'));
		// The Ranked Probability Skill Score is the headline: it is where the
		// blend's advantage over climatology actually shows up.
		expect(blend.rpsSkill).toBeCloseTo(0.129);
		expect(blend.rps).toBeCloseTo(0.479);
		expect(blend.brier).toBeCloseTo(0.576);
		expect(blend.pietra).toBeCloseTo(0.487);
		expect(blend.observations).toBe(26622);
	});

	it('flags the baseline the skill scores are measured against', () => {
		const rows = parsePooledSummary([HEADER, BLEND, BASELINE].join('\n'));
		expect(rows.filter((r) => r.isBaseline).map((r) => r.model)).toEqual(['unc_clim_raw']);
	});

	it('derives per-lead Brier Skill Score from the baseline row', () => {
		const [blend] = parsePooledSummary([HEADER, BLEND, BASELINE].join('\n'));
		// 1 - 0.088/0.091 = 0.0330, and week 3 goes negative: 1 - 0.118/0.117.
		expect(blend.brierSkillByLead[0]).toBeCloseTo(1 - 0.088 / 0.091, 6);
		expect(blend.brierSkillByLead[2]).toBeCloseTo(1 - 0.118 / 0.117, 6);
		expect(blend.brierSkillByLead[2]).toBeLessThan(0);
	});

	it('scores the baseline at exactly zero skill against itself', () => {
		const rows = parsePooledSummary([HEADER, BLEND, BASELINE].join('\n'));
		const baseline = rows.find((r) => r.isBaseline);
		expect(baseline?.brierSkillByLead).toEqual([0, 0, 0, 0, 0]);
	});

	it('yields null per-lead skill when the baseline row is absent', () => {
		// Without unc_clim_raw there is nothing to measure against, and inventing a
		// reference would silently change what the chart claims.
		const [blend] = parsePooledSummary([HEADER, BLEND].join('\n'));
		expect(blend.brierSkillByLead).toEqual([null, null, null, null, null]);
		// The raw per-lead Brier still parses.
		expect(blend.brierByLead[0]).toBeCloseTo(0.088);
	});

	it('yields null rather than Infinity when a baseline Brier is zero', () => {
		const zeroed = 'ALL,0.6,0.5,0.7,1,,,0.3,0,0,0,0,0,0.7,0.6,0.55,0.54,0.6,unc_clim_raw,raw,0,0,0';
		const [blend] = parsePooledSummary([HEADER, BLEND, zeroed].join('\n'));
		expect(blend.brierSkillByLead).toEqual([null, null, null, null, null]);
	});

	it('treats blank cells as missing rather than zero', () => {
		// pandas writes NaN as an empty string; Number('') is 0, which would read
		// as a real score.
		const blanks =
			'ALL,,,0.7,,,,,0.1,0.1,0.1,0.1,0.1,0.7,0.6,0.55,0.54,0.6,blended_model,global,,,';
		const [row] = parsePooledSummary([HEADER, blanks].join('\n'));
		expect(row.rps).toBeNull();
		expect(row.rpsSkill).toBeNull();
		expect(row.pietra).toBeNull();
		expect(row.observations).toBeNull();
	});
});
