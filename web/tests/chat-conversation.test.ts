import { describe, expect, it } from 'vitest';

import type { ChatJobEvent, ChatMessage } from '../src/lib/api';
import { conversationItems } from '../src/lib/chat/conversation';

function turn(id: string, role: 'user' | 'assistant', createdAt: string): ChatMessage {
	return { id, role, content: id, created_at: createdAt };
}

function event(jobId: string, status: ChatJobEvent['status'], at: string): ChatJobEvent {
	return { job_id: jobId, label: 'FuXi', status, at };
}

const turns = [
	turn('ask-1', 'user', '2026-10-08T12:00:00Z'),
	turn('reply-1', 'assistant', '2026-10-08T12:00:00Z'),
	turn('ask-2', 'user', '2026-10-08T12:30:00Z'),
	turn('reply-2', 'assistant', '2026-10-08T12:30:00Z')
];

function keys(items: ReturnType<typeof conversationItems>): string[] {
	return items.map((item) => (item.kind === 'turn' ? item.turn.id : item.event.job_id));
}

describe('job events in the conversation', () => {
	it('places each run before the first message the user sent after it finished', () => {
		const items = conversationItems(
			turns,
			[
				event('late', 'complete', '2026-10-08T12:45:00Z'),
				event('early', 'failed', '2026-10-08T12:10:00Z')
			],
			true
		);

		expect(keys(items)).toEqual(['ask-1', 'reply-1', 'early', 'ask-2', 'reply-2', 'late']);
	});

	it('offers a follow-up only for runs that finished since the user last wrote', () => {
		const items = conversationItems(
			turns,
			[
				event('early', 'failed', '2026-10-08T12:10:00Z'),
				event('late', 'failed', '2026-10-08T12:45:00Z')
			],
			true
		);
		const followUps = items.flatMap((item) =>
			item.kind === 'job_event' ? [[item.event.job_id, item.followUp?.label ?? null]] : []
		);

		expect(followUps).toEqual([
			['early', null],
			['late', 'Find out why']
		]);
	});

	it('holds follow-ups back while the assistant is answering, and has none for a cancellation', () => {
		const late = [event('late', 'complete', '2026-10-08T12:45:00Z')];
		const busy = conversationItems(turns, late, false).at(-1);
		const canceled = conversationItems(
			turns,
			[event('c', 'canceled', '2026-10-08T12:45:00Z')],
			true
		).at(-1);

		expect(busy?.kind === 'job_event' && busy.followUp).toBe(null);
		expect(canceled?.kind === 'job_event' && canceled.followUp).toBe(null);
	});
});
