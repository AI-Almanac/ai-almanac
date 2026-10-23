import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChatMessage, ChatScope, ChatSessionDetail } from '../src/lib/api';
import { tabConversationKey } from '../src/lib/chat/conversation-key';
import { ChatSessionState } from '../src/lib/chat/session.svelte';

const api = vi.hoisted(() => ({
	getChatSessions: vi.fn(),
	getChatSession: vi.fn(),
	getChatJobActivity: vi.fn(),
	getAssistantStartup: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

const scope: ChatScope = { kind: 'general', key: 'conversation-1', job_ids: [] };
const asked: ChatMessage = {
	id: 'u1',
	role: 'user',
	content: 'Which models suit India?',
	created_at: new Date().toISOString()
};

function reply(status: ChatMessage['status'], createdAt = new Date()): ChatMessage {
	return {
		id: 'a1',
		role: 'assistant',
		content: status === 'completed' ? 'AIFS2 and GraphCast.' : '',
		created_at: createdAt.toISOString(),
		status
	};
}

function session(transcript: ChatMessage[]): ChatSessionDetail {
	return { id: 's1', scope, transcript } as unknown as ChatSessionDetail;
}

async function openChat(transcript: ChatMessage[]) {
	api.getChatSessions.mockResolvedValue([{ id: 's1', scope, updated_at: asked.created_at }]);
	api.getChatSession.mockResolvedValue(session(transcript));
	const chat = new ChatSessionState(() => scope);
	chat.syncScope();
	await vi.advanceTimersByTimeAsync(0);
	return chat;
}

describe('a chat reopened while its reply is still being written', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		api.getChatJobActivity.mockResolvedValue({ events: [], active: 0 });
		api.getAssistantStartup.mockResolvedValue(null);
	});
	afterEach(() => {
		vi.useRealTimers();
		vi.clearAllMocks();
	});

	it('shows it as in progress, holds new messages, and shows the reply once it lands', async () => {
		const chat = await openChat([asked, reply('streaming')]);

		expect(chat.messages.map((m) => m.id)).toEqual(['u1']);
		expect(chat.earlierReplyId).toBe('a1');
		expect(chat.canSend).toBe(false);

		api.getChatSession.mockResolvedValue(session([asked, reply('completed')]));
		await vi.advanceTimersByTimeAsync(3000);

		expect(chat.messages.map((m) => m.content)).toEqual([
			'Which models suit India?',
			'AIFS2 and GraphCast.'
		]);
		expect(chat.earlierReplyId).toBeNull();
		expect(chat.canSend).toBe(true);
	});

	it('shows the model startup progress while the reply waits on it', async () => {
		api.getAssistantStartup.mockResolvedValue({
			stage: 'tuning',
			elapsed_seconds: 200,
			detail: 'fp8_gemm',
			done: 3,
			total: 16
		});

		const chat = await openChat([asked, reply('streaming')]);

		expect(chat.assistantStartup).toMatchObject({ stage: 'tuning', previous_failure: null });
	});

	it('treats a reply too old to still be running as interrupted', async () => {
		const chat = await openChat([asked, reply('streaming', new Date(Date.now() - 2 * 3600_000))]);

		expect(chat.earlierReplyId).toBeNull();
		expect(chat.canSend).toBe(true);
		expect(chat.error).toMatch(/interrupted/);
	});

	it('stops checking once the panel is gone', async () => {
		const chat = await openChat([asked, reply('streaming')]);
		chat.dispose();
		const calls = api.getChatSession.mock.calls.length;

		await vi.advanceTimersByTimeAsync(10_000);

		expect(api.getChatSession.mock.calls.length).toBe(calls);
	});
});

describe('tabConversationKey', () => {
	beforeEach(() => sessionStorage.clear());

	it('keeps a page on the same conversation within the tab unless asked for a fresh one', () => {
		const first = tabConversationKey('assistant');

		expect(tabConversationKey('assistant')).toBe(first);
		expect(tabConversationKey('blend-setup')).not.toBe(first);

		const fresh = tabConversationKey('assistant', { fresh: true });
		expect(fresh).not.toBe(first);
		expect(tabConversationKey('assistant')).toBe(fresh);
	});
});
