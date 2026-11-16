import { beforeEach, describe, expect, it } from 'vitest';

import type { WorkspaceArtifact, WorkspaceJob } from '../src/lib/api';
import type { Product } from '../src/lib/workspace/products';
import { groupByRun } from '../src/lib/workspace/runs';
import { checkArrivals, targetView } from '../src/lib/workspace/stage-follow';
import { WorkspaceLayout } from '../src/lib/workspace/workspace-layout.svelte';

describe('the workspace layout', () => {
	beforeEach(() => localStorage.clear());

	it('opens each side as it was left on this device', () => {
		const first = new WorkspaceLayout();
		first.restore(localStorage, 1600);
		first.setProductsOpen(false);
		first.setAssistantOpen(false);

		const next = new WorkspaceLayout();
		next.restore(localStorage, 1600);

		expect(next.zones).toEqual({ productsOpen: false, assistantOpen: false });
		expect(next.widths).toEqual({ products: '3rem', assistant: '3rem' });
	});

	it('marks a reply that lands while the assistant is closed, until it is opened', () => {
		const layout = new WorkspaceLayout();
		layout.setAssistantOpen(false);

		layout.transcriptGrew(2, 3);
		expect(layout.unreadReply).toBe(true);

		layout.setAssistantOpen(true);
		expect(layout.unreadReply).toBe(false);
	});

	it('opens and closes the sheet only for the moment, leaving the column as it was left', () => {
		const layout = new WorkspaceLayout();
		layout.restore(localStorage, 1000);
		layout.form = 'sheet';

		layout.setAssistantOpen(true);
		expect(layout.assistantOpen).toBe(true);
		layout.setAssistantOpen(false);
		expect(layout.assistantOpen).toBe(false);

		const wide = new WorkspaceLayout();
		wide.restore(localStorage, 1600);
		expect(wide.assistantOpen).toBe(true);
	});
});

describe('where the stage goes on its own', () => {
	const run = (key: string, createdAt: string) =>
		({ kind: 'benchmark', key: `run:${key}`, createdAt, run: { key } }) as unknown as Product;

	it('leaves what the workspace held when it loaded, and follows what arrives after', () => {
		const loaded = checkArrivals(null, [run('a', '1')]);
		expect(loaded.arrived).toBeNull();

		const later = checkArrivals(loaded.known, [run('a', '1'), run('b', '2')]);

		expect(later.arrived).toEqual({ kind: 'run', runKey: 'b' });
	});

	it('shows a file the conversation names by its label, or the run its jobs belong to', () => {
		const jobs = [{ id: 'job-1', run_id: 'run-1', created_at: '' }] as WorkspaceJob[];
		const files = [{ id: 'file-1', label: 'onset_map' }] as WorkspaceArtifact[];
		const { runs } = groupByRun(jobs, []);

		expect(targetView({ kind: 'artifact', ref: 'onset_map' }, runs, files)).toEqual({
			kind: 'file',
			artifactId: 'file-1',
			runKey: null
		});
		expect(targetView({ kind: 'run', jobIds: ['job-1'] }, runs, files)).toEqual({
			kind: 'run',
			runKey: 'run-1'
		});
		expect(targetView({ kind: 'artifact', ref: 'missing' }, runs, files)).toBeNull();
	});
});
