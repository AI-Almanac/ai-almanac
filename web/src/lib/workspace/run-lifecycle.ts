/**
 * A run while it is under way and after it stops: what each model is doing, how long
 * it should take, and which of its jobs can still be cancelled.
 */
import type { JobStatus, WorkspaceJob } from '$lib/api';
import { plural } from '$lib/format/plural';
import { modelDisplayName } from '$lib/model-names';
import { RUN_KIND_LABEL, runKind, type Run } from './runs';

export type ModelState = 'waiting' | 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled';

export type ModelProgress = {
	key: string;
	name: string;
	state: ModelState;
	label: string;
	/** e.g. "about 3 minutes, 1 minute so far"; null without an estimate. */
	timing: string | null;
	error: string | null;
};

type ModelLine = { state: ModelState; label: string };

const LINES: Record<JobStatus, ModelLine> = {
	queued: { state: 'waiting', label: 'Waiting to start' },
	starting: { state: 'running', label: 'Starting' },
	running: { state: 'running', label: 'Running' },
	canceling: { state: 'cancelling', label: 'Canceling…' },
	complete: { state: 'done', label: 'Done' },
	failed: { state: 'failed', label: 'Failed' },
	canceled: { state: 'cancelled', label: 'Canceled' }
};

/** How a job's line reads; nothing for a status the table does not know. */
function lineOf(status: string): ModelLine | undefined {
	return Object.hasOwn(LINES, status) ? LINES[status as JobStatus] : undefined;
}

/** What a job is doing; a status the table does not know counts as none of its states. */
function stateOf(status: string): ModelState | undefined {
	return lineOf(status)?.state;
}

/** Waiting or running: still to finish, and still reachable by a cancel. */
function isUnderWay(state: ModelState | undefined): boolean {
	return state === 'waiting' || state === 'running';
}

function isUnfinishedState(state: ModelState | undefined): boolean {
	return isUnderWay(state) || state === 'cancelling';
}

/** `45` → `less than a minute`, `200` → `3 minutes`, `5400` → `1 hour 30 minutes`. */
export function durationWords(seconds: number): string {
	const minutes = Math.round(seconds / 60);
	if (minutes < 1) return 'less than a minute';
	if (minutes < 60) return plural(minutes, 'minute');
	const rest = minutes % 60;
	const hours = plural(Math.floor(minutes / 60), 'hour');
	return rest ? `${hours} ${plural(rest, 'minute')}` : hours;
}

/** The server's timestamps are UTC whether or not they say so. */
export function utcMillis(timestamp: string): number {
	return Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(timestamp) ? timestamp : `${timestamp}Z`);
}

/**
 * How long a job should take against how long it has run. Nothing without an
 * estimate: an elapsed time alone says nothing about how long is left.
 */
export function timingWords(
	expectedSeconds: number | null,
	startedAt: string | null,
	now: number
): string | null {
	if (expectedSeconds === null) return null;
	const words = durationWords(expectedSeconds);
	const expected = expectedSeconds < 30 ? words : `about ${words}`;
	if (!startedAt) return `${expected} once it starts`;
	const elapsed = Math.max(0, (now - utcMillis(startedAt)) / 1000);
	return `${expected}, ${durationWords(elapsed)} so far`;
}

function jobName(job: WorkspaceJob, run: Run): string {
	if (!job.model_name) return RUN_KIND_LABEL[runKind(run)];
	return runKind(run) === 'blend' ? job.model_name : modelDisplayName(job.model_name);
}

/** One line per model, in the order they were submitted. */
export function modelProgress(run: Run, now: number): ModelProgress[] {
	return run.jobs.map((job) => {
		const { state, label } = lineOf(job.status) ?? { state: 'running', label: 'Running' };
		return {
			key: job.id,
			name: jobName(job, run),
			state,
			label,
			timing: isUnderWay(state) ? timingWords(job.expected_seconds, job.started_at, now) : null,
			error: state === 'failed' ? job.error : null
		};
	});
}

export function isUnfinished(progress: ModelProgress[]): boolean {
	return progress.some((model) => isUnfinishedState(model.state));
}

/** Whether the lines say more than the status beside the title already does. */
export function needsProgress(progress: ModelProgress[]): boolean {
	return progress.some((model) => model.state !== 'done');
}

/** Jobs a cancel still reaches: one already cancelling or finished is left alone. */
export function cancellableJobIds(run: Run): string[] {
	return run.jobs.filter((job) => isUnderWay(stateOf(job.status))).map((job) => job.id);
}

export type RunStatus = { label: string; tone: 'running' | 'complete' | 'failed' };

/** The run's status in a few words, telling a cancelled run from a failed one. */
export function runStatusWords(run: Run): RunStatus {
	const states = run.jobs.map((job) => stateOf(job.status));
	const count = (state: ModelState) => states.filter((each) => each === state).length;
	const total = run.jobs.length;
	if (count('cancelling') > 0) return { label: 'Canceling…', tone: 'running' };
	if (states.some(isUnderWay)) {
		const label = total > 1 ? `Running · ${count('done')} of ${total} done` : 'Running';
		return { label, tone: 'running' };
	}
	return stoppedStatus(count('failed'), count('cancelled'), total);
}

function stoppedStatus(failed: number, cancelled: number, total: number): RunStatus {
	if (failed === 0 && cancelled === 0) return { label: 'Complete', tone: 'complete' };
	if (failed === 0) {
		const label = cancelled === total ? 'Canceled' : `${cancelled} of ${total} canceled`;
		return { label, tone: 'failed' };
	}
	const stopped = failed + cancelled;
	return { label: stopped === total ? 'Failed' : `${stopped} of ${total} failed`, tone: 'failed' };
}
