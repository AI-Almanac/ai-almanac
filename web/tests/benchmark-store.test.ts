import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Job } from '../src/lib/api';

const api = vi.hoisted(() => ({
	submitJob: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return {
		...actual,
		submitJob: api.submitJob
	};
});

import { BenchmarkStore } from '../src/lib/benchmarks.svelte';

function queuedJob(): Job {
	return {
		id: 'job-1',
		status: 'queued',
		dataset_id: 'observations-1',
		model_name: 'fuxi-1',
		model_display_name: 'FuXi',
		run_id: 'run-1',
		created_at: '2026-06-08T20:01:00Z',
		params: {
			region: 'ethiopia',
			event_type: 'monsoon_onset'
		}
	};
}

describe('BenchmarkStore', () => {
	beforeEach(() => {
		api.submitJob.mockReset();
	});

	it('does not duplicate a submitted job when the submission callback replays it', async () => {
		const job = queuedJob();
		api.submitJob.mockResolvedValue(job);
		const store = new BenchmarkStore();

		const result = await store.submitRuns({
			datasetId: 'observations-1',
			modelNames: ['fuxi-1'],
			sharedParams: { region: 'ethiopia' }
		});
		store.acceptSubmittedJobs(result.runId, result.jobs);

		expect(store.jobs).toEqual([job]);
		expect(store.runGroups).toHaveLength(1);
		expect(store.runGroups[0].jobs).toHaveLength(1);
	});
});
