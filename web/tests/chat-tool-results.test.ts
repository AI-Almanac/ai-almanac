import { describe, expect, it } from 'vitest';

import { blendSkillTable, skillScoresTable } from '../src/lib/chat/tool-results';
import { toolLabel } from '../src/lib/chat/tools';

describe('the skill tables in a chat', () => {
	const scores = {
		windows: [
			{
				model: 'aifs',
				window: '1-15',
				overall: { ranked_probability_skill_score: 0.12, brier_skill_score: 0.08, auc: 0.52 }
			}
		]
	};

	it('reads the ROC area on its own scale, not as skill above 0', () => {
		const table = skillScoresTable(scores);

		expect(table?.columns).toContain('ROC area');
		expect(table?.caption).toContain('ROC area: 0.5 is no better than chance');
	});

	it('reads a blend member’s ROC area the same way', () => {
		const table = blendSkillTable({
			skill: [{ model: 'aifs', rps_skill: 0.1, brier_skill: 0.05, auc: 0.6 }]
		});

		expect(table?.caption).toContain('ROC area: 0.5 is no better than chance');
	});
});

describe('a step’s label', () => {
	it('says a step failed rather than that it was done', () => {
		const call = { name: 'submit_benchmark', status: 'failed' as const };

		expect(toolLabel(call, 'failed')).toBe('Submitting the benchmark failed');
		expect(toolLabel(call, 'done')).toBe('Submitted the benchmark');
	});
});
