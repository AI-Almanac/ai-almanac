import { describe, expect, it } from 'vitest';

import type { Job } from '../src/lib/api';
import { latestAttempts } from '../src/lib/benchmarks.svelte';

function job(
	id: string,
	model: string,
	createdAt: string,
	params: object = {},
	status = 'failed'
): Job {
	return {
		id,
		model_name: model,
		model_source_id: `source-${model}`,
		created_at: createdAt,
		status,
		params: { start_date: '2000-05-01', end_date: '2025-08-31', ...params }
	} as unknown as Job;
}

describe('latestAttempts', () => {
	it('lists a rerun with the same settings once, as its latest attempt', () => {
		const original = job('a1', 'AIFSv2', '2026-10-23T19:32:50');
		const graphcast = job('g1', 'GraphCast', '2026-10-23T19:32:51', {}, 'complete');
		const retry = job('a2', 'AIFSv2', '2026-10-23T20:07:51', {}, 'running');

		const { jobs, earlierAttempts } = latestAttempts([original, graphcast, retry]);

		// The retry takes the original's place rather than moving to the end.
		expect(jobs.map((j) => j.id)).toEqual(['a2', 'g1']);
		expect(earlierAttempts).toEqual({ a2: [original] });
	});

	it('keeps reruns with changed settings as separate jobs', () => {
		const fullRange = job('f1', 'FuXi', '2026-10-23T19:00:00');
		const oneYear = job('f2', 'FuXi', '2026-10-23T19:10:00', {
			start_date: '2020-05-01',
			end_date: '2020-08-31'
		});

		const { jobs, earlierAttempts } = latestAttempts([fullRange, oneYear]);

		expect(jobs.map((j) => j.id)).toEqual(['f1', 'f2']);
		expect(earlierAttempts).toEqual({});
	});

	it('treats settings as equal whatever order their keys are in', () => {
		const first = job('a1', 'AIFSv2', '2026-10-23T19:00:00', { wet_threshold: 1, window: 30 });
		const second = job('a2', 'AIFSv2', '2026-10-23T20:00:00');
		second.params = {
			window: 30,
			end_date: '2025-08-31',
			wet_threshold: 1,
			start_date: '2000-05-01'
		} as Job['params'];

		expect(latestAttempts([first, second]).jobs.map((j) => j.id)).toEqual(['a2']);
	});
});
