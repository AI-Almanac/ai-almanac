import type { ChatJobEvent, ChatMessage } from '$lib/api';

/** A suggested next message for a run that just changed. */
export type JobFollowUp = { label: string; prompt: string };

export type ConversationItem =
	| { kind: 'turn'; key: string; turn: ChatMessage }
	| { kind: 'job_event'; key: string; event: ChatJobEvent; followUp: JobFollowUp | null };

const STATUS_PHRASES: Record<ChatJobEvent['status'], string> = {
	complete: 'finished',
	failed: 'failed',
	canceled: 'was canceled'
};

export function jobEventText(event: ChatJobEvent): string {
	return `${event.label} ${STATUS_PHRASES[event.status]}`;
}

function followUpFor(event: ChatJobEvent): JobFollowUp | null {
	if (event.status === 'failed') {
		return { label: 'Find out why', prompt: `Why did the ${event.label} run fail?` };
	}
	if (event.status === 'complete') {
		return { label: 'Summarize results', prompt: `Summarize the ${event.label} results.` };
	}
	return null;
}

function time(value: string): number {
	return new Date(value).getTime();
}

/**
 * The conversation in time order: each run that finished goes before the first
 * message the user sent after it. Only runs that finished since the user last
 * wrote offer a follow-up; older ones have already been part of the conversation.
 */
export function conversationItems(
	turns: ChatMessage[],
	events: ChatJobEvent[],
	canFollowUp: boolean
): ConversationItem[] {
	const pending = [...events].sort((a, b) => time(a.at) - time(b.at));
	const items: ConversationItem[] = [];
	const eventItem = (event: ChatJobEvent, latest: boolean): ConversationItem => ({
		kind: 'job_event',
		key: `job-${event.job_id}-${event.status}`,
		event,
		followUp: latest && canFollowUp ? followUpFor(event) : null
	});

	for (const turn of turns) {
		if (turn.role === 'user') {
			while (pending.length && time(pending[0].at) < time(turn.created_at)) {
				items.push(eventItem(pending.shift()!, false));
			}
		}
		items.push({ kind: 'turn', key: turn.id, turn });
	}
	items.push(...pending.map((event) => eventItem(event, true)));
	return items;
}
