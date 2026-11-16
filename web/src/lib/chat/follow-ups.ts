import type { ChatMessage, FollowUp } from '$lib/api';

const SETTLED: FollowUp['status'][] = ['done', 'failed'];

export function isQueued(followUp: FollowUp): boolean {
	return followUp.status === 'waiting' || followUp.status === 'running';
}

/** Ids of follow-ups that have finished their chat turn. */
export function settledIds(followUps: FollowUp[]): Set<string> {
	return new Set(followUps.filter((f) => SETTLED.includes(f.status)).map((f) => f.id));
}

/** Whether a follow-up in `sessionId` finished since `previous`, so its transcript has a new turn. */
export function sessionGainedTurn(
	previous: Set<string>,
	current: FollowUp[],
	sessionId: string | null
): boolean {
	return current.some(
		(f) => f.session_id === sessionId && SETTLED.includes(f.status) && !previous.has(f.id)
	);
}

const FOLLOW_UP_TRIGGER = 'follow_up:';

/** A turn the platform started when queued runs finished, not something the user wrote. */
export function isFollowUpTurn(message: Pick<ChatMessage, 'trigger'>): boolean {
	return message.trigger?.startsWith(FOLLOW_UP_TRIGGER) ?? false;
}

/** Ids of the assistant replies the platform started, each right after its follow-up turn. */
export function followUpReplyIds(
	messages: Pick<ChatMessage, 'id' | 'role' | 'trigger'>[]
): Set<string> {
	return new Set(
		messages.flatMap((message, i) =>
			message.role === 'assistant' && i > 0 && isFollowUpTurn(messages[i - 1]) ? [message.id] : []
		)
	);
}
