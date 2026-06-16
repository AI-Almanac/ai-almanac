import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import ChatPanel from '../src/lib/components/ChatPanel.svelte';
import type { ChatScope, ChatSession, ChatSessionDetail, Job } from '../src/lib/api';

vi.mock('../src/lib/components/FigureCard.svelte', () => ({
	default: {}
}));

vi.mock('../src/lib/components/FigureLightbox.svelte', () => ({
	default: {}
}));

const api = vi.hoisted(() => ({
	createChatSession: vi.fn(),
	getChatSessions: vi.fn(),
	getChatSession: vi.fn(),
	updateChatSession: vi.fn(),
	deleteChatSession: vi.fn(),
	sendChatMessage: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return {
		...actual,
		createChatSession: api.createChatSession,
		getChatSessions: api.getChatSessions,
		getChatSession: api.getChatSession,
		updateChatSession: api.updateChatSession,
		deleteChatSession: api.deleteChatSession,
		sendChatMessage: api.sendChatMessage
	};
});

function deferred<T>() {
	let resolve!: (value: T | PromiseLike<T>) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function baseScope(jobIds = ['job-1']): ChatScope {
	return {
		kind: 'benchmark_run_group',
		key: 'scope-a',
		title: 'Scope A',
		job_ids: jobIds
	};
}

function baseSession(overrides: Partial<ChatSession> = {}): ChatSession {
	return {
		id: 'session-1',
		title: 'Session 1',
		created_at: '2026-04-23T00:00:00Z',
		updated_at: '2026-04-23T00:00:00Z',
		message_count: 0,
		scope: baseScope(),
		...overrides
	};
}

function sessionDetail(transcript: ChatSessionDetail['transcript']): ChatSessionDetail {
	return {
		...baseSession(),
		transcript
	};
}

function jobs(jobIds = ['job-1']): Job[] {
	return jobIds.map((id) => ({
		id,
		status: 'complete',
		dataset_id: 'dataset-1',
		model_name: `Model ${id}`,
		model_display_name: `Model ${id}`,
		params: {
			event_type: 'rainfall',
			region: 'sahel',
			start_date: '2024-01-01',
			end_date: '2024-02-01'
		}
	}));
}

async function settle() {
	await Promise.resolve();
	await Promise.resolve();
}

describe('ChatPanel', () => {
	it('reloads authoritative session state after a send failure', async () => {
		api.getChatSessions.mockResolvedValue([baseSession()]);
		api.getChatSession.mockResolvedValueOnce(sessionDetail([])).mockResolvedValueOnce(
			sessionDetail([
				{
					id: 'turn-user-1',
					role: 'user',
					content: 'Explain the top error source.',
					created_at: '2026-04-23T00:00:00Z'
				},
				{
					id: 'turn-assistant-1',
					role: 'assistant',
					content: 'Recovered answer from server state.',
					created_at: '2026-04-23T00:00:01Z'
				}
			])
		);
		api.sendChatMessage.mockImplementation(async function* () {
			yield { type: 'text_delta', turn_id: 'turn-assistant-1', content: 'Partial' };
			throw new Error('backend stream failed');
		});

		render(ChatPanel, { jobs: jobs(), scopeKey: 'scope-a' });

		await screen.findByText('Ask a question about the benchmark results above.');

		const input = screen.getByPlaceholderText(
			'Ask about the results… (Enter to send, Shift+Enter for newline)'
		);
		await fireEvent.input(input, { target: { value: 'Explain the top error source.' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Send' }));

		await screen.findByText('Recovered answer from server state.');
		await screen.findByText('backend stream failed');

		expect(screen.queryByText('Partial')).toBeNull();
		expect(api.getChatSession).toHaveBeenCalledTimes(2);
	});

	it('disables send controls while a request is in flight', async () => {
		api.getChatSessions.mockResolvedValue([baseSession()]);
		api.getChatSession.mockResolvedValue(sessionDetail([]));
		const gate = deferred<void>();
		api.sendChatMessage.mockImplementation(async function* () {
			await gate.promise;
			yield {
				type: 'done',
				turn: {
					id: 'turn-assistant-1',
					role: 'assistant',
					content: 'Completed',
					created_at: '2026-04-23T00:00:01Z'
				}
			};
		});

		render(ChatPanel, { jobs: jobs(), scopeKey: 'scope-a' });

		await screen.findByText('Ask a question about the benchmark results above.');

		const input = screen.getByPlaceholderText(
			'Ask about the results… (Enter to send, Shift+Enter for newline)'
		);
		await fireEvent.input(input, { target: { value: 'Run once.' } });
		const sendButton = screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement;

		await fireEvent.click(sendButton);
		await settle();

		expect(api.sendChatMessage).toHaveBeenCalledTimes(1);
		expect(sendButton.disabled).toBe(true);

		gate.resolve();

		await screen.findByText('Completed');
		expect(screen.queryByText('Run once.')).not.toBeNull();
	});

	it('shows a specific message for provider stream errors', async () => {
		api.getChatSessions.mockResolvedValue([baseSession()]);
		api.getChatSession.mockResolvedValueOnce(sessionDetail([])).mockResolvedValueOnce(
			sessionDetail([
				{
					id: 'turn-user-1',
					role: 'user',
					content: 'Summarize this benchmark.',
					created_at: '2026-04-23T00:00:00Z'
				},
				{
					id: 'turn-assistant-1',
					role: 'assistant',
					content: '',
					created_at: '2026-04-23T00:00:01Z'
				}
			])
		);
		api.sendChatMessage.mockImplementation(async function* () {
			yield {
				type: 'error',
				error_type: 'provider_error',
				message: 'Chat response failed'
			};
		});

		render(ChatPanel, { jobs: jobs(), scopeKey: 'scope-a' });

		await screen.findByText('Ask a question about the benchmark results above.');

		const input = screen.getByPlaceholderText(
			'Ask about the results… (Enter to send, Shift+Enter for newline)'
		);
		await fireEvent.input(input, { target: { value: 'Summarize this benchmark.' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Send' }));

		await screen.findByText('The chat provider failed. Please try again.');
	});
});
