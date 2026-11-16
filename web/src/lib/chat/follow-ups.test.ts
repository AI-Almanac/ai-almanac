import { describe, expect, it } from 'vitest';
import type { FollowUp } from '$lib/api';
import { sessionGainedTurn, settledIds } from './follow-ups';

const followUp = (id: string, status: FollowUp['status'], session_id = 's1'): FollowUp => ({
	id,
	session_id,
	job_ids: ['j1'],
	instruction: 'compare the blends',
	status,
	created_at: '2026-09-24T00:00:00Z'
});

describe('sessionGainedTurn', () => {
	it('fires once when a follow-up in the open session finishes', () => {
		const before = [followUp('f1', 'running')];
		const after = [followUp('f1', 'done')];
		expect(sessionGainedTurn(settledIds(before), after, 's1')).toBe(true);
		expect(sessionGainedTurn(settledIds(after), after, 's1')).toBe(false);
	});

	it('ignores other sessions and follow-ups still waiting', () => {
		expect(sessionGainedTurn(new Set(), [followUp('f1', 'done', 's2')], 's1')).toBe(false);
		expect(sessionGainedTurn(new Set(), [followUp('f1', 'waiting')], 's1')).toBe(false);
	});
});
