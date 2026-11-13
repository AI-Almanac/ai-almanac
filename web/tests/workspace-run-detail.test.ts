import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import RunDetail from '../src/lib/components/workspace/RunDetail.svelte';
import type { WorkspaceArtifact, WorkspaceJob } from '../src/lib/api';
import type { Run } from '../src/lib/workspace/runs';
import { workspaceArtifact, workspaceJob } from './fixtures/workspace';

vi.mock('../src/lib/components/blend/BlendSkillPanel.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/ResultsViewer.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/AllMetricsPanel.svelte', async () => ({
	default: (await import('./fixtures/AllMetricsStub.svelte')).default
}));

const api = vi.hoisted(() => ({ getBlendSummary: vi.fn(), getJob: vi.fn(), cancelJob: vi.fn() }));
vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});
const skillApi = vi.hoisted(() => ({ getSkillByArea: vi.fn() }));
vi.mock('../src/lib/api/skill-by-area', () => skillApi);

const SUMMARY = [
	'id,brier,rps,auc,n,lat,lon,pietra,brier_week1,brier_week2,brier_week3,brier_week4,brier_later,auc_week1,auc_week2,auc_week3,auc_week4,auc_later,model,cv_method,brier_skill,rps_skill,AUC diff',
	'ALL,0.576,0.479,0.835,26622,,,0.487,0.088,0.113,0.118,0.119,0.137,0.890,0.787,0.737,0.716,0.881,blended_model,global,0.036,0.129,0.62'
].join('\n');

const blend = workspaceJob({
	id: 'blend-1',
	job_type: 'blend',
	run_id: 'run-b',
	model_name: 'AIFS-FuXi Ethiopia Blend',
	scope: { place: 'Ethiopia', years: { start: 2008, end: 2024 }, lead_days: null }
});

function summaryFile(jobId: string): WorkspaceArtifact {
	return workspaceArtifact({
		id: 'art-summary',
		label: 'summary_models_pooled_2008_2024',
		title: 'Summary models pooled · 2008–2024',
		group: 'Tables',
		job_id: jobId,
		run_job_ids: [jobId],
		kind: 'table',
		format: 'csv',
		breadcrumb: ['blend.job']
	});
}

function renderRun(run: Run) {
	return render(RunDetail, {
		run,
		workspaceId: 'ws-1',
		onOpenFile: vi.fn(),
		onDownload: vi.fn()
	});
}

function benchmark(overrides: Partial<WorkspaceJob>): WorkspaceJob {
	return workspaceJob({ id: 'bench-1', run_id: 'run-x', ...overrides });
}

describe('RunDetail for a benchmark under way', () => {
	const underWay: Run = {
		key: 'run-x',
		files: [],
		jobs: [
			benchmark({ id: 'bench-1', model_name: 'aifs', status: 'complete' }),
			benchmark({
				id: 'bench-2',
				model_name: 'fuxi',
				status: 'running',
				started_at: new Date(Date.now() - 70_000).toISOString(),
				expected_seconds: 180
			}),
			benchmark({ id: 'bench-3', model_name: 'fuxi_s2s', status: 'queued' })
		]
	};

	beforeEach(() => {
		api.getJob.mockReset().mockImplementation(async (id: string) => ({ id, status: 'complete' }));
		api.cancelJob.mockReset().mockResolvedValue({});
	});

	it('says what each model is doing and how long the running one should take', () => {
		renderRun(underWay);

		const lines = within(screen.getByRole('region', { name: 'Run progress' }))
			.getAllByRole('listitem')
			.map((line) => line.textContent?.replace(/\s+/g, ' ').trim());
		expect(lines).toEqual([
			expect.stringMatching(/AIFS Done$/),
			expect.stringMatching(/FuXi Running · about 3 minutes, 1 minute so far$/),
			expect.stringMatching(/FuXi S2S Waiting to start$/)
		]);
		expect(screen.getByText(/2 models are still running/)).toBeTruthy();
	});

	it('cancels every unfinished model only after a confirmation, and says it is cancelling', async () => {
		renderRun(underWay);

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
		expect(api.cancelJob).not.toHaveBeenCalled();
		await fireEvent.click(screen.getByRole('button', { name: 'Yes, cancel run' }));

		expect(api.cancelJob.mock.calls.map(([id]) => id)).toEqual(['bench-2', 'bench-3']);
		expect((await screen.findByRole('status')).textContent).toContain('Canceling…');
		expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull();
	});

	it('keeps the run going when the confirmation is declined', async () => {
		renderRun(underWay);

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Keep running' }));

		expect(api.cancelJob).not.toHaveBeenCalled();
		expect(screen.getByRole('button', { name: 'Cancel run' })).toBeTruthy();
	});

	it('says so when the run could not be cancelled, and offers to try again', async () => {
		api.cancelJob.mockRejectedValue(new Error('The run has already stopped'));
		renderRun(underWay);

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Yes, cancel run' }));

		expect((await screen.findByRole('alert')).textContent).toContain('The run has already stopped');
		expect(screen.getByRole('button', { name: 'Cancel run' })).toBeTruthy();
	});
});

describe('RunDetail for a finished benchmark', () => {
	const finished: Run = {
		key: 'run-x',
		files: [],
		jobs: [benchmark({ status: 'complete', area_of_interest: ['West Gojjam'] })]
	};

	beforeEach(() => {
		api.getJob.mockReset().mockImplementation(async (id: string) => ({ id, status: 'complete' }));
		skillApi.getSkillByArea.mockReset().mockResolvedValue({
			areas_from: 'area_of_interest',
			level: 'adm2',
			models: ['aifs'],
			windows: ['1-15'],
			rows: [],
			not_covered: ['West Gojjam']
		});
	});

	it('has no progress or cancel to show', async () => {
		renderRun(finished);

		await screen.findByRole('tab', { name: 'Models and map' });
		expect(screen.queryByRole('region', { name: 'Run progress' })).toBeNull();
		expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull();
	});

	it('opens the by-zone view for this run from the stage', async () => {
		renderRun(finished);

		await fireEvent.click(await screen.findByRole('tab', { name: 'By zone' }));

		await waitFor(() => expect(skillApi.getSkillByArea).toHaveBeenCalledWith('ws-1', 'run-x', {}));
		expect(await screen.findByText('Not covered by this run')).toBeTruthy();
	});

	it('shows every metric of the run in place, with no link out to another page', async () => {
		renderRun(finished);

		await fireEvent.click(await screen.findByRole('tab', { name: 'All metrics' }));

		expect(await screen.findByTestId('all-metrics')).toBeTruthy();
		expect(screen.queryByRole('link', { name: /Benchmarks page/ })).toBeNull();
	});

	it('names the model that failed and why', () => {
		renderRun({
			...finished,
			jobs: [benchmark({ status: 'failed', error: 'The observations could not be read.' })]
		});

		const progress = screen.getByRole('region', { name: 'Run progress' });
		expect(progress.textContent).toContain('AIFS');
		expect(progress.textContent).toContain('The observations could not be read.');
		expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull();
	});
});

describe('RunDetail for a blend', () => {
	beforeEach(() => {
		api.getBlendSummary.mockReset().mockResolvedValue(SUMMARY);
		api.getJob.mockReset();
	});

	it('shows the forecast skill of a finished blend in place', async () => {
		renderRun({ key: 'run-b', jobs: [blend], files: [summaryFile('blend-1')] });

		expect(await screen.findByRole('heading', { name: 'Forecast skill' })).toBeTruthy();
		expect(api.getBlendSummary).toHaveBeenCalledWith('blend-1');
		expect(screen.queryByRole('link', { name: /blend/i })).toBeNull();
	});

	it('waits for the skill summary to be published before loading it', async () => {
		renderRun({ key: 'run-b', jobs: [blend], files: [] });

		await waitFor(() => expect(api.getBlendSummary).not.toHaveBeenCalled());
		expect(screen.queryByRole('heading', { name: 'Forecast skill' })).toBeNull();
	});
});
