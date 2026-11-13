/** Where a run stands, in the words and colours the run lists and cards show. */
import type { JobStatus } from '$lib/api';
import type { RunStatus } from '$lib/components/RunSidebar.svelte';

type StatusFacts = { name: string; active: boolean };

const STATUSES: Record<JobStatus, StatusFacts> = {
	queued: { name: 'Queued', active: true },
	starting: { name: 'Starting', active: true },
	running: { name: 'Running', active: true },
	canceling: { name: 'Canceling', active: true },
	canceled: { name: 'Canceled', active: false },
	complete: { name: 'Complete', active: false },
	failed: { name: 'Failed', active: false }
};

/** What the table says of a status; nothing for one it does not know. */
function factsOf(status: string): StatusFacts | undefined {
	return Object.hasOwn(STATUSES, status) ? STATUSES[status as JobStatus] : undefined;
}

/** The run is still going: it can be canceled, not deleted, and is worth polling. */
export function isActiveStatus(status: string): boolean {
	return factsOf(status)?.active ?? false;
}

/** The status itself, e.g. "Queued". */
export function statusName(status: string): string {
	return factsOf(status)?.name ?? status.charAt(0).toUpperCase() + status.slice(1);
}

/** A run's badge, where waiting to start reads as running: the user has nothing to do either way. */
export function badgeLabel(status: string): string {
	return isActiveStatus(status) && status !== 'canceling' ? 'Running' : statusName(status);
}

/** The colour a badge takes; none for a run that was canceled. */
export function badgeTone(status: string): 'complete' | 'failed' | 'running' | '' {
	if (status === 'complete' || status === 'failed') return status;
	return isActiveStatus(status) ? 'running' : '';
}

/** When a run was made or finished, e.g. "Jun 12, 3:40 PM"; a dash when it has no time. */
export function runTime(value?: string | null): string {
	if (!value) return '—';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return new Intl.DateTimeFormat(undefined, {
		month: 'short',
		day: 'numeric',
		hour: 'numeric',
		minute: '2-digit'
	}).format(date);
}

/** One run's standing in a run list. */
export function sidebarStatus(status: string): RunStatus {
	if (status === 'complete' || status === 'failed' || status === 'canceled') return status;
	return isActiveStatus(status) ? 'running' : 'mixed';
}
