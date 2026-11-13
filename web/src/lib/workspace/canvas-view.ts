/** What the right-hand canvas is showing; mirrored in the URL query. */
export type CanvasView =
	| { kind: 'overview' }
	/** `window` opens the run's map on that lead-time window, e.g. "1-15". */
	| { kind: 'run'; runKey: string; window?: string }
	/** `runKey` remembers which run the file was opened from, so Back returns there. */
	| { kind: 'file'; artifactId: string; runKey: string | null };

export const OVERVIEW: CanvasView = { kind: 'overview' };

export function parseCanvasView(params: URLSearchParams): CanvasView {
	const artifactId = params.get('artifact');
	const runKey = params.get('run');
	if (artifactId) return { kind: 'file', artifactId, runKey };
	const window = params.get('window');
	if (runKey) return window ? { kind: 'run', runKey, window } : { kind: 'run', runKey };
	return OVERVIEW;
}

/** Rewrites only the canvas keys, leaving any other query params in place. */
export function canvasQuery(view: CanvasView, current: URLSearchParams): string {
	const params = new URLSearchParams(current);
	params.delete('run');
	params.delete('artifact');
	params.delete('window');
	if (view.kind === 'run') {
		params.set('run', view.runKey);
		if (view.window) params.set('window', view.window);
	}
	if (view.kind === 'file') {
		params.set('artifact', view.artifactId);
		if (view.runKey) params.set('run', view.runKey);
	}
	const query = params.toString();
	return query ? `?${query}` : '';
}

export function backFrom(view: CanvasView): CanvasView {
	if (view.kind === 'file' && view.runKey) return { kind: 'run', runKey: view.runKey };
	return OVERVIEW;
}

export type BackLink = { label: string; view: CanvasView };

/**
 * Where a file's Back goes: the run it was opened from, else the newest run. A
 * workspace without runs has neither, so there is no link rather than one that
 * leads back to the same file.
 */
export function backLink(view: CanvasView, hasRuns: boolean): BackLink | null {
	if (view.kind !== 'file' || !hasRuns) return null;
	return { label: view.runKey ? '← Back to run' : '← Newest run', view: backFrom(view) };
}
