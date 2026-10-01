import { describe, it, expect } from 'vitest';

import { groupBlendOutputs, parseYearlyScores } from '../src/routes/blends/blend-outputs';

const files = [
	'run.log',
	'combined_wide.pkl',
	'summary_models_pooled.csv',
	'summary_models.pkl',
	'summary_models.csv',
	'yearly_metrics_global.pkl',
	'yearly_metrics_global.csv',
	'coefs_blended_model_global_year2015.pkl',
	'coefs_blended_model_global_final.pkl',
	'coefs_blended_model_global_final.csv',
	'training_spec.yml',
	'manifest.json'
].map((filename) => ({ filename }));

describe('groupBlendOutputs', () => {
	it('puts the CV scores, final weights and training settings ahead of everything else', () => {
		const groups = groupBlendOutputs(files);
		expect(groups.map((g) => [g.key, g.files.map((f) => f.filename)])).toEqual([
			['cv', ['summary_models_pooled.csv', 'summary_models.csv', 'yearly_metrics_global.csv']],
			['final', ['coefs_blended_model_global_final.pkl', 'coefs_blended_model_global_final.csv']],
			['spec', ['training_spec.yml']],
			[
				'other',
				[
					'run.log',
					'combined_wide.pkl',
					'summary_models.pkl',
					'yearly_metrics_global.pkl',
					'coefs_blended_model_global_year2015.pkl',
					'manifest.json'
				]
			]
		]);
	});

	it('drops groups with no files', () => {
		expect(groupBlendOutputs([{ filename: 'run.log' }]).map((g) => g.key)).toEqual(['other']);
	});
});

describe('parseYearlyScores', () => {
	it('reads one row per holdout year, sorted by year', () => {
		const csv = [
			'year,model,cv_method,brier,rps,auc',
			'2016,blended_model,global,0.61,0.50,0.82',
			'2015,blended_model,global,0.58,,nan'
		].join('\n');
		expect(parseYearlyScores(csv)).toEqual([
			{ year: 2015, model: 'blended_model', brier: 0.58, rps: null, auc: null },
			{ year: 2016, model: 'blended_model', brier: 0.61, rps: 0.5, auc: 0.82 }
		]);
	});

	it('returns nothing for an empty or unrecognised file', () => {
		expect(parseYearlyScores('')).toEqual([]);
		expect(parseYearlyScores('a,b\n1,2')).toEqual([]);
	});
});
