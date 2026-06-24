import { describe, it, expect } from 'vitest';

import { parsePooledSummary } from '../src/routes/blends/blend-summary';

// Trimmed real header + two rows from a blend's summary_models_pooled CSV.
const HEADER =
	'id,brier,rps,auc,n,lat,lon,pietra,brier_week1,brier_week2,brier_week3,brier_week4,brier_later,auc_week1,auc_week2,auc_week3,auc_week4,auc_later,model,cv_method,brier_skill,rps_skill,AUC diff';
const BLEND =
	'ALL,0.576,0.479,0.835,26622,,,0.487,0.088,0.113,0.118,0.119,0.137,0.890,0.787,0.737,0.716,0.881,blended_model,global,0.036,0.129,0.62';
const AIFS =
	'ALL,1.036,0.993,0.676,26622,,,0.352,0.140,0.166,0.187,0.210,0.331,0.665,0.570,0.536,0.555,0.700,aifs_clim_mok_date_raw,raw,-0.73,-0.80,-15.3';

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
		const unc =
			'ALL,0.6,0.5,0.7,1,,,0.3,0,0,0,0,0,0.7,0.6,0.55,0.54,0.6,unc_clim_raw,raw,0,0,0';
		const labels = parsePooledSummary([HEADER, cal, unc].join('\n')).map((r) => r.label);
		expect(labels).toContain('FUXI (calibrated)');
		expect(labels).toContain('Climatology (uncalibrated)');
	});

	it('returns [] when the header lacks AUC columns', () => {
		expect(parsePooledSummary('id,model\nALL,blended_model')).toEqual([]);
	});

	it('returns [] for empty input', () => {
		expect(parsePooledSummary('')).toEqual([]);
	});
});
