import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { WorkspaceJob } from '../src/lib/api';
import RunPicker from '../src/lib/components/workspace/RunPicker.svelte';
import { addableRunDetail, addableRuns, findAddableRuns } from '../src/lib/workspace/addable-runs';
import { workspaceJob } from './fixtures/workspace';

function job(overrides: Partial<WorkspaceJob>): WorkspaceJob {
	return workspaceJob({ run_id: 'run-1', ...overrides });
}

const jobs = [
	job({ id: 'b-1' }),
	job({ id: 'b-2', model_name: 'fuxi' }),
	job({
		id: 'blend-1',
		run_id: null,
		job_type: 'blend',
		model_name: 'AIFS-FuXi Ethiopia Blend',
		scope: { place: 'trained 2008–2018', years: null, lead_days: null },
		status: 'running',
		created_at: '2026-10-01T00:00:00Z'
	})
];

describe('runs that can be added', () => {
	it('groups jobs submitted together into one run, named as the products list names it', () => {
		const runs = addableRuns(jobs);

		expect(runs.map((item) => [item.kind, item.title, item.status.label])).toEqual([
			['Blend', 'AIFS-FuXi Ethiopia Blend · trained 2008–2018', 'Running'],
			['Benchmark', 'Ethiopia Kiremt onset · AIFS, FuXi', 'Complete']
		]);
	});

	it('says a canceled run was canceled, not that it failed', () => {
		const [canceled] = addableRuns([job({ status: 'canceled' })]);

		expect(canceled.status.label).toBe('Canceled');
		expect(addableRunDetail(canceled)).not.toContain('Failed');
	});

	it('finds a run by a part of its name or by its kind', () => {
		const runs = addableRuns(jobs);

		expect(findAddableRuns(runs, ' kiremt ').map((item) => item.run.key)).toEqual(['run-1']);
		expect(findAddableRuns(runs, 'blend').map((item) => item.run.key)).toEqual(['blend-1']);
		expect(findAddableRuns(runs, '')).toHaveLength(2);
	});
});

describe('RunPicker', () => {
	it('lists runs by name, never by id, and adds the one that is pressed', async () => {
		const onAdd = vi.fn().mockResolvedValue(undefined);
		render(RunPicker, { load: async () => jobs, onAdd, onClose: vi.fn() });

		const run = await screen.findByRole('button', { name: /Ethiopia Kiremt onset · AIFS, FuXi/ });
		expect(document.body.textContent).not.toMatch(/run-1|b-1|blend-1/);
		await fireEvent.click(run);

		expect(onAdd).toHaveBeenCalledTimes(1);
		expect(onAdd.mock.calls[0][0].jobs.map((item: WorkspaceJob) => item.id)).toEqual([
			'b-1',
			'b-2'
		]);
	});

	it('narrows the list as a name is typed, and says when nothing matches', async () => {
		render(RunPicker, { load: async () => jobs, onAdd: vi.fn(), onClose: vi.fn() });
		await screen.findByRole('button', { name: /Ethiopia Kiremt onset/ });

		const find = screen.getByRole('searchbox', { name: 'Find a run by name' });
		await fireEvent.input(find, { target: { value: 'blend' } });
		expect(screen.queryByRole('button', { name: /Ethiopia Kiremt onset/ })).toBeNull();
		expect(screen.getByRole('button', { name: /AIFS-FuXi Ethiopia Blend/ })).toBeTruthy();

		await fireEvent.input(find, { target: { value: 'sahel' } });
		expect(screen.getByText('No run has “sahel” in its name.')).toBeTruthy();
	});

	it('says so when every run is already in the workspace', async () => {
		render(RunPicker, { load: async () => [], onAdd: vi.fn(), onClose: vi.fn() });

		expect(await screen.findByText(/Every run of yours is already here/)).toBeTruthy();
	});

	it('keeps the list open with the reason when a run cannot be added', async () => {
		const onAdd = vi.fn().mockRejectedValue(new Error('nope'));
		render(RunPicker, { load: async () => jobs, onAdd, onClose: vi.fn() });

		await fireEvent.click(await screen.findByRole('button', { name: /Ethiopia Kiremt onset/ }));

		expect((await screen.findByRole('alert')).textContent).toMatch(/could not be added/);
		expect(screen.getByRole('button', { name: /Ethiopia Kiremt onset/ })).toBeTruthy();
	});

	it('closes without adding anything on Cancel', async () => {
		const onAdd = vi.fn();
		const onClose = vi.fn();
		render(RunPicker, { load: async () => jobs, onAdd, onClose });

		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

		expect(onClose).toHaveBeenCalled();
		expect(onAdd).not.toHaveBeenCalled();
	});
});
