import { describe, expect, it } from 'vitest';

import type { WorkspaceJob } from '../src/lib/api';
import {
	cancellableJobIds,
	deletableJobIds,
	durationWords,
	isUnfinished,
	modelProgress,
	needsProgress,
	noFilesWords,
	runStatusWords,
	timingWords,
	utcMillis
} from '../src/lib/workspace/run-lifecycle';
import type { Run } from '../src/lib/workspace/runs';
import { workspaceJob } from './fixtures/workspace';

function run(...jobs: WorkspaceJob[]): Run {
	return { key: 'run-1', jobs, files: [] };
}

/** A run with one job in each of the given statuses. */
function runOf(...statuses: string[]): Run {
	return run(...statuses.map((status, i) => workspaceJob({ id: `j${i}`, status })));
}

const NOW = Date.parse('2026-11-01T10:01:10Z');

describe('how long a run takes, in words', () => {
	it.each([
		[20, 'less than a minute'],
		[70, '1 minute'],
		[200, '3 minutes'],
		[5400, '1 hour 30 minutes'],
		[7200, '2 hours']
	])('says %i seconds as "%s"', (seconds, words) => {
		expect(durationWords(seconds)).toBe(words);
	});

	it('sets the estimate against the time so far', () => {
		expect(timingWords(180, '2026-11-01T10:00:00Z', NOW)).toBe('about 3 minutes, 1 minute so far');
	});

	it('says nothing without an estimate, and only the estimate before the start', () => {
		expect(timingWords(null, '2026-11-01T10:00:00Z', NOW)).toBeNull();
		expect(timingWords(180, null, NOW)).toBe('about 3 minutes once it starts');
	});

	it('reads server timestamps as UTC whether or not they say so', () => {
		expect(utcMillis('2026-11-01T10:00:00')).toBe(Date.parse('2026-11-01T10:00:00Z'));
		expect(utcMillis('2026-11-01T12:00:00+02:00')).toBe(Date.parse('2026-11-01T10:00:00Z'));
	});
});

describe('per-model progress', () => {
	const mixed = run(
		workspaceJob({ id: 'a', model_name: 'aifs', status: 'complete' }),
		workspaceJob({
			id: 'b',
			model_name: 'fuxi',
			status: 'running',
			started_at: '2026-11-01T10:00:00Z',
			expected_seconds: 180
		}),
		workspaceJob({ id: 'c', model_name: 'graphcast', status: 'queued' }),
		workspaceJob({
			id: 'd',
			model_name: 'gencast',
			status: 'failed',
			error: 'The forecast files are missing.'
		})
	);

	it('says what each model is doing, with timing only where there is an estimate', () => {
		const lines = modelProgress(mixed, NOW);
		expect(lines.map((line) => line.label)).toEqual([
			'Done',
			'Running',
			'Waiting to start',
			'Failed'
		]);
		expect(lines.map((line) => line.timing)).toEqual([
			null,
			'about 3 minutes, 1 minute so far',
			null,
			null
		]);
		expect(lines[3].error).toBe('The forecast files are missing.');
		expect(isUnfinished(lines)).toBe(true);
	});

	it('names a blend by its own name and a nameless job by its kind', () => {
		const blend = run(workspaceJob({ job_type: 'blend', model_name: 'AIFS-FuXi Ethiopia Blend' }));
		expect(modelProgress(blend, NOW)[0].name).toBe('AIFS-FuXi Ethiopia Blend');
		const forecast = run(workspaceJob({ job_type: 'forecast', model_name: null }));
		expect(modelProgress(forecast, NOW)[0].name).toBe('Forecast');
	});

	it('has nothing to add to a run that finished cleanly', () => {
		expect(needsProgress(modelProgress(runOf('complete'), NOW))).toBe(false);
		expect(needsProgress(modelProgress(mixed, NOW))).toBe(true);
	});

	it('cancels only what has not finished or begun cancelling', () => {
		expect(cancellableJobIds(runOf('complete', 'running', 'queued', 'canceling'))).toEqual([
			'j1',
			'j2'
		]);
	});
});

describe('deleting a run', () => {
	it('takes every job of a run that has stopped, however it ended', () => {
		expect(deletableJobIds(runOf('failed', 'complete', 'canceled'))).toEqual(['j0', 'j1', 'j2']);
	});

	it('takes nothing while any job is still going', () => {
		for (const going of ['queued', 'running', 'canceling', 'status-from-the-future']) {
			expect(deletableJobIds(runOf('failed', going))).toEqual([]);
		}
	});

	it('takes nothing when any job is not the user’s to delete', () => {
		const partlyShared = run(
			workspaceJob({ id: 'a', status: 'failed' }),
			workspaceJob({ id: 'b', status: 'failed', deletable: false })
		);
		expect(deletableJobIds(partlyShared)).toEqual([]);
	});
});

describe('run status in words', () => {
	it.each([
		[['complete', 'running', 'queued'], 'Running · 1 of 3 done', 'running'],
		[['canceling'], 'Canceling…', 'running'],
		[['canceled'], 'Canceled', 'failed'],
		[['complete', 'canceled'], '1 of 2 canceled', 'failed'],
		[['complete', 'failed'], '1 of 2 failed', 'failed'],
		[['failed'], 'Failed', 'failed'],
		[['complete', 'complete'], 'Complete', 'complete']
	])('reads %j as "%s"', (statuses, label, tone) => {
		expect(runStatusWords(runOf(...statuses))).toEqual({ label, tone });
	});
});

describe('a status this page does not know yet', () => {
	it('reads as still going in the run’s status, its cancel and its progress alike', () => {
		const run = runOf('complete', 'provisioning');

		expect(runStatusWords(run)).toEqual({ label: 'Running · 1 of 2 done', tone: 'running' });
		expect(cancellableJobIds(run)).toEqual(['j1']);
		expect(modelProgress(run, NOW)[1].state).toBe('running');
	});
});

describe('a run with no files to show', () => {
	it.each([
		[['running'], 'Files appear here when the run finishes.'],
		[['complete', 'queued'], 'Files appear here when the run finishes.'],
		[['complete'], 'Preparing the files…'],
		[['complete', 'failed'], 'Preparing the files…'],
		[['failed', 'canceled'], 'This run made no files.']
	])('with jobs %j says "%s"', (statuses, words) => {
		expect(noFilesWords(runOf(...statuses))).toBe(words);
	});
});
