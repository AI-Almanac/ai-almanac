/**
 * A blend's day-level blend can train in its own run alongside the week-level
 * one. The blend completes with the week-level blend, so its results show while
 * the day-level blend is still training; its files and skill rows join them
 * once it finishes.
 */
import type { Blend } from '$lib/api/blends';
import type { JobArtifact } from '$lib/api/jobs';

// The day-level run's own files, published next to the week-level ones.
const DAY_LEVEL_PREFIX = 'day_level_';

type BlendState = Pick<Blend, 'status' | 'day_level_status'>;

/** The blend is complete but its day-level blend is still training. */
export function dayLevelTraining(blend: BlendState): boolean {
	return blend.status === 'complete' && blend.day_level_status === 'running';
}

/** Whether the blend can still change: its own run, or a day-level blend after it. */
export function blendInProgress(blend: BlendState, activeStatuses: readonly string[]): boolean {
	return activeStatuses.includes(blend.status) || dayLevelTraining(blend);
}

/** Whether a refreshed blend differs in anything the page renders from it. */
export function blendStateChanged(before: BlendState, after: BlendState): boolean {
	return before.status !== after.status || before.day_level_status !== after.day_level_status;
}

/**
 * Whether files are still on their way to a complete blend's list: indexing
 * lags completion by a few seconds, once for the blend and again for a
 * day-level blend that finished after it.
 */
export function awaitingArtifacts(blend: BlendState, artifacts: JobArtifact[]): boolean {
	if (artifacts.length === 0) return true;
	return (
		blend.day_level_status === 'complete' &&
		!artifacts.some((artifact) => artifact.filename.startsWith(DAY_LEVEL_PREFIX))
	);
}

/** Where the results stand on the day-level blend, or null when there is nothing to say. */
export function dayLevelNote(
	blend: Pick<Blend, 'status' | 'day_level_status' | 'day_level_error'>
): { kind: 'training' | 'failed' | 'stopped'; title: string; detail: string | null } | null {
	if (blend.status !== 'complete') return null;
	switch (blend.day_level_status) {
		case 'running':
			return {
				kind: 'training',
				title: 'Day-level blend still training',
				detail: 'Its skill and files will be added here when it finishes.'
			};
		case 'failed':
			return {
				kind: 'failed',
				title: 'Day-level blend failed',
				detail: blend.day_level_error ?? null
			};
		case 'canceled':
			return {
				kind: 'stopped',
				title: 'Day-level blend stopped',
				detail: blend.day_level_error ?? null
			};
		default:
			return null;
	}
}
