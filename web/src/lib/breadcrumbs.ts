// In-memory breadcrumb ring buffer for feedback context.
//
// Records the user's recent activity (API calls, navigations, errors) so a
// feedback submission can carry a fine-grained trail of what led up to it.
// Nothing here is transmitted anywhere except as part of an explicit feedback
// submission; the buffer is capped and lives only for the page session.

export type BreadcrumbType = 'api' | 'navigation' | 'error' | 'action';

export type Breadcrumb = {
	/** Epoch milliseconds. */
	ts: number;
	type: BreadcrumbType;
	/** Short human-readable summary, e.g. "GET /jobs 200 (43ms)". */
	message: string;
	/** Structured details (status codes, request IDs, routes, stacks). */
	data?: Record<string, unknown>;
};

const MAX_BREADCRUMBS = 100;
/** Truncate long strings (error bodies, stacks) so the payload stays small. */
const MAX_FIELD_LENGTH = 500;

const buffer: Breadcrumb[] = [];

function truncate(value: unknown): unknown {
	if (typeof value === 'string' && value.length > MAX_FIELD_LENGTH) {
		return `${value.slice(0, MAX_FIELD_LENGTH)}… (${value.length} chars)`;
	}
	return value;
}

export function addBreadcrumb(
	type: BreadcrumbType,
	message: string,
	data?: Record<string, unknown>
): void {
	const crumb: Breadcrumb = { ts: Date.now(), type, message: String(truncate(message)) };
	if (data) {
		crumb.data = Object.fromEntries(Object.entries(data).map(([k, v]) => [k, truncate(v)]));
	}
	buffer.push(crumb);
	if (buffer.length > MAX_BREADCRUMBS) buffer.splice(0, buffer.length - MAX_BREADCRUMBS);
}

/** Snapshot of the current trail, oldest first. */
export function getBreadcrumbs(): Breadcrumb[] {
	return [...buffer];
}

/** Test helper. */
export function clearBreadcrumbs(): void {
	buffer.length = 0;
}
