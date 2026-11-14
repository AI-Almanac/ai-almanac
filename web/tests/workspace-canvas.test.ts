import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { WorkspaceDetail } from '../src/lib/api';
import WorkspaceCanvas from '../src/lib/components/workspace/WorkspaceCanvas.svelte';
import { groupByRun } from '../src/lib/workspace/runs';
import { workspaceJob } from './fixtures/workspace';

vi.mock('../src/lib/components/workspace/RunDetail.svelte', async () => ({
	default: (await import('./fixtures/RunProbe.svelte')).default
}));
vi.mock('../src/lib/components/workspace/ArtifactDetail.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

describe('the stage when another run is opened', () => {
	const workspace = {
		id: 'ws-1',
		jobs: [
			workspaceJob({ id: 'job-a', run_id: 'run-a' }),
			workspaceJob({ id: 'job-b', run_id: 'run-b' })
		],
		artifacts: []
	} as unknown as WorkspaceDetail;
	const results = groupByRun(workspace.jobs, workspace.artifacts);

	it('starts the run view afresh instead of handing the old one the new run', async () => {
		const onNavigate = vi.fn();
		const { rerender } = render(WorkspaceCanvas, {
			workspace,
			results,
			view: { kind: 'run', runKey: 'run-a' },
			onNavigate
		});
		expect(screen.getByTestId('run-probe').textContent).toBe('showing run-a, created for run-a');

		await rerender({ workspace, results, view: { kind: 'run', runKey: 'run-b' }, onNavigate });

		expect(screen.getByTestId('run-probe').textContent).toBe('showing run-b, created for run-b');
	});
});
