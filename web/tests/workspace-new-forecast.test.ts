import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { WorkspaceJob } from '../src/lib/api';

const api = vi.hoisted(() => ({
	listBlends: vi.fn(),
	getForecastModels: vi.fn(),
	getInitSources: vi.fn(),
	createForecast: vi.fn()
}));
vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

import NewForecastPanel from '../src/lib/components/workspace/NewForecastPanel.svelte';
import { workspaceJob } from './fixtures/workspace';

const blendJobs = [
	workspaceJob({ id: 'blend-1', job_type: 'blend', created_at: '2026-09-01T00:00:00Z' }),
	workspaceJob({ id: 'blend-2', job_type: 'blend', created_at: '2026-10-01T00:00:00Z' }),
	workspaceJob({
		id: 'blend-3',
		job_type: 'blend',
		status: 'running',
		created_at: '2026-11-01T00:00:00Z'
	})
];

beforeEach(() => {
	vi.clearAllMocks();
	api.listBlends.mockResolvedValue([
		{ id: 'blend-1', name: 'Older blend', status: 'complete', model_names: ['aifs_eth'] },
		{
			id: 'blend-2',
			name: 'Kiremt blend',
			status: 'complete',
			model_names: ['aifs_eth'],
			forecast_models: { aifs_eth: 'aifs' }
		},
		{ id: 'blend-3', name: 'Still training', status: 'running', model_names: ['aifs_eth'] }
	]);
	api.getForecastModels.mockResolvedValue([{ id: 'aifs', display_name: 'AIFS' }]);
	api.getInitSources.mockResolvedValue([{ id: 'gfs', display_name: 'GFS analysis' }]);
	api.createForecast.mockResolvedValue({ id: 'forecast-9' });
});

function renderPanel(jobs: WorkspaceJob[] = blendJobs) {
	const onStarted = vi.fn().mockResolvedValue(undefined);
	const onCancel = vi.fn();
	render(NewForecastPanel, { jobs, onStarted, onCancel });
	return { onStarted, onCancel };
}

describe('NewForecastPanel', () => {
	it('opens on the workspace’s newest finished blend and offers only finished blends', async () => {
		renderPanel();

		const blend = (await screen.findByLabelText('Blend')) as HTMLSelectElement;

		expect(blend.value).toBe('blend-2');
		expect(screen.getByRole('option', { name: 'Older blend' })).toBeTruthy();
		expect(screen.queryByRole('option', { name: 'Still training' })).toBeNull();
	});

	it('leaves the blend to be chosen in a workspace that has none', async () => {
		renderPanel([]);

		expect(((await screen.findByLabelText('Blend')) as HTMLSelectElement).value).toBe('');
		expect(
			(screen.getByRole('button', { name: 'Run forecast' }) as HTMLButtonElement).disabled
		).toBe(true);
	});

	it('runs the forecast and hands it to the workspace', async () => {
		const { onStarted } = renderPanel();

		await fireEvent.click(await screen.findByLabelText('AIFS'));
		await fireEvent.click(screen.getByRole('button', { name: 'Run forecast' }));

		await waitFor(() => expect(onStarted).toHaveBeenCalledWith(['forecast-9']));
		expect(api.createForecast).toHaveBeenCalledWith({
			blend_id: 'blend-2',
			forecast_model_ids: ['aifs_eth'],
			params: { init_source: 'gfs' }
		});
	});

	it('says so when the forecast started but could not be added to the workspace', async () => {
		const { onStarted } = renderPanel();
		onStarted.mockRejectedValue(new Error('offline'));

		await fireEvent.click(await screen.findByLabelText('AIFS'));
		await fireEvent.click(screen.getByRole('button', { name: 'Run forecast' }));

		expect((await screen.findByRole('alert')).textContent).toContain('could not be added');
	});

	it('closes without running anything on Cancel', async () => {
		const { onCancel } = renderPanel();

		await fireEvent.click((await screen.findAllByRole('button', { name: 'Cancel' }))[0]);

		expect(onCancel).toHaveBeenCalled();
		expect(api.createForecast).not.toHaveBeenCalled();
	});
});
