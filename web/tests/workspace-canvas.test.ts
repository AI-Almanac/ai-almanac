import { fireEvent, render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { WorkspaceDetail } from '../src/lib/api';
import WorkspaceCanvas from '../src/lib/components/workspace/WorkspaceCanvas.svelte';
import { KEPT_VIEWS } from '../src/lib/workspace/canvas-view';
import { groupByRun } from '../src/lib/workspace/runs';
import { mounts } from './fixtures/RunProbe.svelte';
import { workspaceJob } from './fixtures/workspace';

vi.mock('../src/lib/components/workspace/RunDetail.svelte', async () => ({
	default: (await import('./fixtures/RunProbe.svelte')).default
}));
vi.mock('../src/lib/components/workspace/ArtifactDetail.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

const runKeys = Array.from({ length: KEPT_VIEWS + 1 }, (_, i) => `run-${i}`);
const workspace = {
	id: 'ws-1',
	jobs: runKeys.map((key) => workspaceJob({ id: `job-${key}`, run_id: key })),
	artifacts: []
} as unknown as WorkspaceDetail;
const results = groupByRun(workspace.jobs, workspace.artifacts);
const onNavigate = vi.fn();

function openRuns(...keys: string[]) {
	const [first, ...rest] = keys;
	const { rerender } = render(WorkspaceCanvas, {
		workspace,
		results,
		view: { kind: 'run', runKey: first },
		onNavigate
	});
	return rest.reduce(
		(shown, key) =>
			shown.then(() =>
				rerender({ workspace, results, view: { kind: 'run', runKey: key }, onNavigate })
			),
		Promise.resolve()
	);
}

/** Whether an element sits inside one made inert (jsdom keeps `inert` as a property only). */
function isInert(element: HTMLElement | null): boolean {
	for (let node = element; node; node = node.parentElement) if (node.inert) return true;
	return false;
}

/** The run view on show; the others are kept out of sight and out of reach. */
function shownProbe(): string {
	const shown = screen.getAllByTestId('run-probe').filter((probe) => !isInert(probe));
	expect(shown).toHaveLength(1);
	return shown[0].textContent ?? '';
}

beforeEach(() => {
	mounts.length = 0;
});

describe('the stage when another run is opened', () => {
	it('starts the run view afresh instead of handing the old one the new run', async () => {
		await openRuns('run-0', 'run-1');

		expect(shownProbe()).toBe('showing run-1, created for run-1');
	});

	it('finds a run viewed a moment ago as it was left, without making it again', async () => {
		await openRuns('run-0', 'run-1', 'run-0');

		expect(shownProbe()).toBe('showing run-0, created for run-0');
		expect(mounts).toEqual(['run-0', 'run-1']);
	});

	it('lets go of the run viewed longest ago once more are open than it keeps', async () => {
		await openRuns(...runKeys, 'run-0');

		expect(screen.getAllByTestId('run-probe')).toHaveLength(KEPT_VIEWS);
		expect(mounts.filter((key) => key === 'run-0')).toHaveLength(2);
	});
});

describe('updating the latest forecast', () => {
	const forecastUpdate = { jobId: 'job-run-1', request: vi.fn() };

	function showRun(runKey: string) {
		render(WorkspaceCanvas, {
			workspace,
			results,
			view: { kind: 'run', runKey },
			onNavigate,
			forecastUpdate
		});
	}

	it('is offered on that forecast’s run and asks for the update', async () => {
		showRun('run-1');

		await fireEvent.click(screen.getByRole('button', { name: 'Update forecast' }));
		expect(forecastUpdate.request).toHaveBeenCalled();
	});

	it('is not offered on other runs', () => {
		showRun('run-0');

		expect(screen.queryByRole('button', { name: 'Update forecast' })).toBeNull();
	});
});
