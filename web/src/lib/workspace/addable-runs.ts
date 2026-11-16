/** Runs made elsewhere that can be added to a workspace, named as its products list would name them. */
import type { WorkspaceJob } from '$lib/api/workspaces';
import { runStatusWords, utcMillis, type RunStatus } from './run-lifecycle';
import { groupByRun, RUN_KIND_LABEL, runKind, runTitle, type Run } from './runs';

export type AddableRun = {
	run: Run;
	title: string;
	kind: string;
	status: RunStatus;
	/** When its newest job was submitted. */
	startedAt: string;
};

function newestSubmission(run: Run): string {
	return run.jobs.reduce((latest, job) => (job.created_at > latest ? job.created_at : latest), '');
}

export function addableRuns(jobs: WorkspaceJob[]): AddableRun[] {
	return groupByRun(jobs, []).runs.map((run) => ({
		run,
		title: runTitle(run),
		kind: RUN_KIND_LABEL[runKind(run)],
		status: runStatusWords(run),
		startedAt: newestSubmission(run)
	}));
}

/** The runs with the query in their name or kind; all of them for an empty query. */
export function findAddableRuns(runs: AddableRun[], query: string): AddableRun[] {
	const wanted = query.trim().toLowerCase();
	if (!wanted) return runs;
	return runs.filter((item) => `${item.title} ${item.kind}`.toLowerCase().includes(wanted));
}

/** "Benchmark · 10 Nov 2026", with the status only when the run is not simply finished. */
export function addableRunDetail(item: AddableRun): string {
	const day = item.startedAt
		? new Date(utcMillis(item.startedAt)).toLocaleDateString(undefined, { dateStyle: 'medium' })
		: null;
	const status = item.status.tone === 'complete' ? null : item.status.label;
	return [item.kind, status, day].filter(Boolean).join(' · ');
}
