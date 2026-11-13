import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Job, WorkspaceJob } from '../src/lib/api';
import type { FocusAreaValue } from '../src/lib/api/jobs';

const api = vi.hoisted(() => ({
	getRegions: vi.fn(),
	getDatasets: vi.fn(),
	getRompDefaults: vi.fn(),
	getGuardrailThresholds: vi.fn(),
	getJob: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

vi.mock('../src/lib/components/benchmark-setup/BenchmarkForm.svelte', async () => ({
	default: (await import('./fixtures/BenchmarkFormProbe.svelte')).default
}));

import NewBenchmarkPanel from '../src/lib/components/workspace/NewBenchmarkPanel.svelte';

function workspaceJob(overrides: Partial<WorkspaceJob>): WorkspaceJob {
	return {
		id: 'job-1',
		job_type: 'benchmark',
		status: 'complete',
		run_id: 'run-old',
		created_at: '2026-09-22T00:00:00Z',
		...overrides
	} as WorkspaceJob;
}

function job(overrides: Partial<Job>): Job {
	return {
		id: 'job-1',
		region_id: 'ethiopia',
		dataset_id: 'chirps',
		model_name: 'aifs',
		model_source_id: 'src-aifs',
		params: { wet_threshold: 20 },
		...overrides
	} as Job;
}

/** What the form was started from, once its choices have loaded. */
async function formStart() {
	const shown = screen.getByTestId('form-start');
	await waitFor(() => expect(shown.textContent?.trim()).not.toBe('loading'));
	return JSON.parse(shown.textContent ?? 'null');
}

describe('starting a benchmark from the workspace', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		api.getRegions.mockResolvedValue([]);
		api.getDatasets.mockResolvedValue([{ id: 'chirps', region: 'ethiopia' }]);
		api.getRompDefaults.mockResolvedValue(null);
		api.getGuardrailThresholds.mockResolvedValue(null);
		api.getJob.mockImplementation(async (id: string) => job({ id }));
	});

	it('starts from the workspace’s most recent benchmark, and says so', async () => {
		render(NewBenchmarkPanel, {
			jobs: [workspaceJob({ id: 'job-1' })],
			onStarted: vi.fn(),
			onCancel: vi.fn()
		});

		expect(await formStart()).toEqual({
			regionId: 'ethiopia',
			datasetId: 'chirps',
			modelIds: ['src-aifs'],
			sharedParams: { wet_threshold: 20 }
		});
		expect(api.getJob).toHaveBeenCalledWith('job-1');
		expect(screen.getByText(/most recent benchmark/)).toBeTruthy();
	});

	it('takes the assistant’s draft over the last benchmark', async () => {
		render(NewBenchmarkPanel, {
			jobs: [workspaceJob({ id: 'job-1' })],
			draft: { dataset_id: 'chirps', model_ids: ['graphcast'] },
			onStarted: vi.fn(),
			onCancel: vi.fn()
		});

		expect(await formStart()).toMatchObject({
			regionId: 'ethiopia',
			datasetId: 'chirps',
			modelIds: ['graphcast']
		});
		expect(screen.getByText(/Filled in by the assistant/)).toBeTruthy();
	});

	it('starts a first benchmark in the workspace’s area', async () => {
		const area: FocusAreaValue = { level: 'adm1', units: ['Amhara'] };
		render(NewBenchmarkPanel, { jobs: [], area, onStarted: vi.fn(), onCancel: vi.fn() });

		expect(await formStart()).toMatchObject({ sharedParams: { focus_area: area } });
		expect(api.getJob).not.toHaveBeenCalled();
	});

	it('puts the submitted run’s jobs into the workspace', async () => {
		const onStarted = vi.fn().mockResolvedValue(undefined);
		render(NewBenchmarkPanel, { jobs: [], onStarted, onCancel: vi.fn() });
		await formStart();

		await fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }));

		expect(onStarted).toHaveBeenCalledWith(['new-aifs', 'new-fuxi']);
		expect(screen.queryByRole('alert')).toBeNull();
	});

	it('says how to add the run by hand when it could not be put into the workspace', async () => {
		const onStarted = vi.fn().mockRejectedValue(new Error('offline'));
		render(NewBenchmarkPanel, { jobs: [], onStarted, onCancel: vi.fn() });
		await formStart();

		await fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }));

		expect((await screen.findByRole('alert')).textContent).toContain(
			'The benchmark started, but it could not be added to this workspace'
		);
	});

	it('closes on Cancel', async () => {
		const onCancel = vi.fn();
		render(NewBenchmarkPanel, { jobs: [], onStarted: vi.fn(), onCancel });

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

		expect(onCancel).toHaveBeenCalled();
		expect(screen.getByRole('heading', { name: 'New benchmark' })).toBeTruthy();
	});
});
