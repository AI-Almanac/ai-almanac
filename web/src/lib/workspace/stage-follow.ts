/**
 * Where the stage goes when it is not opened by hand: onto new work as it
 * arrives, and onto what the conversation or a headline fact points at.
 */
import type { WorkspaceArtifact } from '$lib/api/workspaces';
import type { WorkspaceTarget } from '$lib/interface-actions/actions';
import type { CanvasView } from './canvas-view';
import { newestArrival, productView, seenProducts, type Product } from './products';
import { findRunByJobs, resolveArtifact, type Run } from './runs';

export type ArrivalCheck = {
	/** What the stage has now had the chance to follow. */
	known: Set<string>;
	/** Where the newest arrival opens, or null when nothing arrived. */
	arrived: CanvasView | null;
};

/**
 * What arrived since `known` was taken. The first check, with nothing known yet,
 * finds no arrival: what the workspace already held when it loaded is not new.
 */
export function checkArrivals(
	known: ReadonlySet<string> | null,
	products: Product[]
): ArrivalCheck {
	const arrival = known ? newestArrival(known, products) : null;
	return { known: seenProducts(products), arrived: arrival ? productView(arrival) : null };
}

/** The run any of the jobs belong to. */
export function jobsView(runs: Run[], jobIds: string[]): CanvasView | null {
	const run = findRunByJobs(runs, jobIds);
	return run ? { kind: 'run', runKey: run.key } : null;
}

export function fileView(file: WorkspaceArtifact): CanvasView {
	return { kind: 'file', artifactId: file.id, runKey: null };
}

/** Where something the conversation names is shown; null when the workspace does not hold it. */
export function targetView(
	target: WorkspaceTarget,
	runs: Run[],
	artifacts: WorkspaceArtifact[]
): CanvasView | null {
	if (target.kind === 'run') return jobsView(runs, target.jobIds);
	const file = resolveArtifact(artifacts, target.ref);
	return file ? fileView(file) : null;
}
