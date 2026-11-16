import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatPanel from '../src/lib/components/ChatPanel.svelte';
import { BETA_NOTICE } from '../src/lib/chat/column';
import type { ChatScope, ChatSession, ChatSessionDetail, Job } from '../src/lib/api';
import type { WorkspaceTarget } from '../src/lib/interface-actions/actions';

vi.mock('../src/lib/benchmarks.svelte', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/benchmarks.svelte')>(
		'../src/lib/benchmarks.svelte'
	);
	return { ...actual, fetchResultBlob: vi.fn(async () => 'blob:figure') };
});

const api = vi.hoisted(() => ({
	createChatSession: vi.fn(),
	getChatSessions: vi.fn(),
	getChatSession: vi.fn(),
	getRulesetOptions: vi.fn(),
	updateChatSession: vi.fn(),
	deleteChatSession: vi.fn(),
	sendChatMessage: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

beforeEach(() => {
	for (const mock of Object.values(api)) mock.mockReset();
	api.getRulesetOptions.mockResolvedValue([]);
	api.getChatSessions.mockResolvedValue([]);
	localStorage.clear();
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

describe('ChatPanel', () => {
	async function renderIdleChat(props: Record<string, unknown> = {}) {
		api.getChatSessions.mockResolvedValue([baseSession()]);
		api.getChatSession.mockResolvedValue(sessionDetail([]));
		const view = render(ChatPanel, { jobs: jobs(), scopeKey: 'scope-a', ...props });
		await screen.findByText('Ask a question about the benchmark results above.');
		return view;
	}

	async function ask(text: string) {
		await fireEvent.input(screen.getByRole('textbox'), { target: { value: text } });
		await fireEvent.click(screen.getByRole('button', { name: 'Send' }));
	}

	it('keeps its toolbar above the conversation outside a workspace', async () => {
		await renderIdleChat();

		expect(screen.getByTitle('Switch session')).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Chat options' })).toBeTruthy();
		expect(screen.queryByRole('button', { name: 'Conversation menu' })).toBeNull();
	});

	it('keeps the beta notice on screen until it is dismissed', async () => {
		const first = await renderIdleChat();
		expect(screen.getByText(BETA_NOTICE)).toBeTruthy();
		first.unmount();

		const second = await renderIdleChat();
		await fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
		expect(screen.queryByText(BETA_NOTICE)).toBeNull();
		second.unmount();

		await renderIdleChat();
		expect(screen.queryByText(BETA_NOTICE)).toBeNull();
		expect(screen.getByText('Beta').getAttribute('title')).toBe(BETA_NOTICE);
	});

	it('reports when the assistant starts and stops working', async () => {
		const gate = deferred<void>();
		api.sendChatMessage.mockImplementation(async function* () {
			await gate.promise;
			yield {
				type: 'done',
				turn: { id: 'turn-assistant-1', role: 'assistant', content: 'Completed', created_at: '' }
			};
		});
		const onWorkingChange = vi.fn();
		await renderIdleChat({ onWorkingChange });
		expect(onWorkingChange).not.toHaveBeenCalled();

		await ask('Run once.');
		await waitFor(() => expect(onWorkingChange.mock.calls).toEqual([[true]]));

		gate.resolve();
		await screen.findByText('Completed');
		await waitFor(() => expect(onWorkingChange.mock.calls).toEqual([[true], [false]]));
	});

	it('stops reporting work once a reply ends by asking for approval', async () => {
		api.sendChatMessage.mockImplementation(async function* () {
			yield {
				type: 'tool_approval_request',
				turn_id: 'turn-assistant-1',
				tool_call: {
					id: 'call-1',
					name: 'run_forecast',
					status: 'pending',
					input: {},
					artifacts: []
				},
				metadata: {}
			};
		});
		const onWorkingChange = vi.fn();
		await renderIdleChat({ onWorkingChange });

		await ask('Run the forecast.');

		await waitFor(() => expect(onWorkingChange.mock.calls).toEqual([[true], [false]]));
	});

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

		const input = screen.getByRole('textbox');
		await fireEvent.input(input, { target: { value: 'Explain the top error source.' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Send' }));

		await screen.findByText('Recovered answer from server state.');
		await screen.findByText('backend stream failed');

		expect(screen.queryByText('Partial')).toBeNull();
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

		const input = screen.getByRole('textbox');
		await fireEvent.input(input, { target: { value: 'Run once.' } });
		const sendButton = screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement;

		await fireEvent.click(sendButton);

		await waitFor(() => expect(sendButton.disabled).toBe(true));
		expect(api.sendChatMessage).toHaveBeenCalledTimes(1);

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

		const input = screen.getByRole('textbox');
		await fireEvent.input(input, { target: { value: 'Summarize this benchmark.' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Send' }));

		await screen.findByText('The chat provider failed. Please try again.');
	});
});

describe('ChatPanel in a workspace', () => {
	const session = baseSession({
		title: 'Workspace',
		message_count: 4,
		scope: { kind: 'workspace', key: 'ws-1', title: 'Workspace', job_ids: [] }
	});

	const detail: ChatSessionDetail = {
		...session,
		transcript: [
			{
				id: 'u1',
				role: 'user',
				content: 'Compare AIFS and FuXi',
				created_at: '2026-09-22T00:00:00Z',
				trigger: 'user'
			},
			{
				id: 'f1',
				role: 'user',
				content: 'The runs you queued have finished. Continue.',
				created_at: '2026-09-22T01:00:00Z',
				trigger: 'follow_up:fu-1'
			},
			{
				id: 'a1',
				role: 'assistant',
				content: 'AIFS has the lower false alarm rate.',
				created_at: '2026-09-22T01:00:01Z',
				source_artifacts: [
					{ id: 'art-1', label: 'far_aifs' },
					{ id: 'art-2', label: 'far_fuxi' }
				]
			}
		]
	};

	const figureDetail: ChatSessionDetail = {
		...session,
		transcript: [
			{
				id: 'a9',
				role: 'assistant',
				content: 'Here is the map.',
				created_at: '2026-09-22T02:00:00Z',
				source_artifacts: [{ id: 'art-1', label: 'far_aifs' }],
				tool_calls: [
					{
						id: 'call-9',
						name: 'plot_map',
						status: 'completed',
						input: {},
						artifacts: [
							{
								id: 'fig-9',
								workspace_artifact_id: 'ws-art-9',
								kind: 'figure',
								url: '/api/figures/fig-9',
								label: 'Onset map',
								filename: 'onset_map_3f2a.webp',
								created_at: '2026-09-22T02:00:00Z'
							}
						]
					}
				],
				blocks: [
					{ kind: 'tool', tool_call_id: 'call-9' },
					{ kind: 'text', text: 'Here is the map.' }
				]
			}
		]
	};

	async function renderWorkspaceChat(
		shown: ChatSessionDetail = detail,
		props: {
			onOpenItem?: (target: WorkspaceTarget) => void;
			fileTitles?: Record<string, string>;
		} = {
			onOpenItem: vi.fn()
		}
	) {
		api.getChatSessions.mockResolvedValue([session]);
		api.getChatSession.mockResolvedValue(shown);
		render(ChatPanel, { scopeKind: 'workspace', scopeKey: 'ws-1', ...props });
		await screen.findByText(shown.transcript.at(-1)!.content);
	}

	function withTurns(...turns: ChatSessionDetail['transcript']): ChatSessionDetail {
		return { ...detail, transcript: [...detail.transcript, ...turns] };
	}

	it('shows a follow-up turn as a note from the platform, not as the user speaking', async () => {
		await renderWorkspaceChat();

		expect(screen.getByText('Picked up after your runs finished')).toBeTruthy();
		expect(screen.queryByText('The runs you queued have finished. Continue.')).toBeNull();
		expect(screen.getByText('Compare AIFS and FuXi')).toBeTruthy();
	});

	it('says so when a reply failed or ended with nothing to show', async () => {
		api.getChatSessions.mockResolvedValue([session]);
		api.getChatSession.mockResolvedValue(
			withTurns(
				{ id: 'a2', role: 'assistant', content: '', created_at: '', status: 'failed' },
				{ id: 'a3', role: 'assistant', content: '', created_at: '', status: 'completed' }
			)
		);
		render(ChatPanel, { scopeKind: 'workspace', scopeKey: 'ws-1', onOpenItem: vi.fn() });

		expect(await screen.findByText(/This reply didn't finish/)).toBeTruthy();
		expect(screen.getByText('The assistant ended its turn without replying.')).toBeTruthy();
	});

	it('does not ask the user to resend a follow-up the platform started', async () => {
		api.getChatSessions.mockResolvedValue([session]);
		api.getChatSession.mockResolvedValue(
			withTurns(
				{
					id: 'resend-u',
					role: 'user',
					content: 'The runs you queued work on have finished.',
					created_at: '',
					trigger: 'follow_up:fu-1'
				},
				{ id: 'resend-a', role: 'assistant', content: '', created_at: '', status: 'failed' }
			)
		);
		render(ChatPanel, { scopeKind: 'workspace', scopeKey: 'ws-1', onOpenItem: vi.fn() });

		expect(await screen.findByText(/couldn't finish its report on your runs/)).toBeTruthy();
		expect(screen.queryByText(/Send your message again/)).toBeNull();
	});

	it('ends a reply with one line counting its sources, which opens to each file', async () => {
		const onOpenItem = vi.fn();
		await renderWorkspaceChat(detail, { onOpenItem });

		expect(screen.queryByRole('button', { name: 'Open far_fuxi' })).toBeNull();
		await fireEvent.click(screen.getByRole('button', { name: /Based on 2 files/ }));
		await fireEvent.click(screen.getByRole('button', { name: 'Open far_fuxi' }));

		expect(onOpenItem).toHaveBeenCalledWith({ kind: 'artifact', ref: 'art-2' });
	});

	it('names the files an answer was based on as they are called in the workspace now', async () => {
		await renderWorkspaceChat(detail, {
			onOpenItem: vi.fn(),
			fileTitles: { 'art-2': 'FuXi false alarms' }
		});
		await fireEvent.click(screen.getByRole('button', { name: /Based on 2 files/ }));

		expect(screen.getByRole('button', { name: 'Open FuXi false alarms' })).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Open far_aifs' })).toBeTruthy();
	});

	it('folds the toolbar into one menu beside the composer', async () => {
		await renderWorkspaceChat();

		expect(screen.queryByTitle('Switch session')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Rename' })).toBeNull();

		await fireEvent.click(screen.getByRole('button', { name: 'Conversation menu' }));

		expect(screen.getByRole('button', { name: '+ New Chat' })).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy();
		expect(screen.getAllByRole('button', { name: 'Rename' }).length).toBeGreaterThan(0);
	});

	it('renames the conversation from the menu', async () => {
		await renderWorkspaceChat();
		await fireEvent.click(screen.getByRole('button', { name: 'Conversation menu' }));
		await fireEvent.click(screen.getByTitle('Rename chat'));

		expect(screen.getByRole('textbox', { name: 'Conversation name' })).toBeTruthy();
	});

	it('shows the beta notice in full once, then keeps it on the badge', async () => {
		const first = render(ChatPanel, { scopeKind: 'workspace', scopeKey: 'ws-1' });
		expect(await screen.findByText(BETA_NOTICE)).toBeTruthy();
		first.unmount();

		await renderWorkspaceChat();

		expect(screen.queryByText(BETA_NOTICE)).toBeNull();
		await fireEvent.click(screen.getByRole('button', { name: 'Beta: about the assistant' }));
		expect(screen.getByRole('note').textContent).toContain(BETA_NOTICE);

		await fireEvent.keyDown(screen.getByRole('note'), { key: 'Escape' });
		expect(screen.queryByText(BETA_NOTICE)).toBeNull();
		expect(document.activeElement).toBe(
			screen.getByRole('button', { name: 'Beta: about the assistant' })
		);
	});

	it('leaves the notice owed on the other pages after a workspace has shown it', async () => {
		const inWorkspace = render(ChatPanel, { scopeKind: 'workspace', scopeKey: 'ws-1' });
		expect(await screen.findByText(BETA_NOTICE)).toBeTruthy();
		inWorkspace.unmount();

		render(ChatPanel, { scopeKind: 'benchmark_run_group', scopeKey: 'run-1' });

		expect(await screen.findByText(BETA_NOTICE)).toBeTruthy();
	});

	it('shows a figure as a one-line card that opens it in the workspace', async () => {
		const onOpenItem = vi.fn();
		await renderWorkspaceChat(figureDetail, { onOpenItem });

		expect(screen.queryByRole('img')).toBeNull();
		await fireEvent.click(screen.getByRole('button', { name: 'Open Onset map' }));

		expect(onOpenItem).toHaveBeenCalledWith({ kind: 'artifact', ref: 'ws-art-9' });
	});

	it('draws a figure in the conversation when nothing else can open it', async () => {
		await renderWorkspaceChat(figureDetail, {});

		expect(await screen.findByRole('img', { name: 'Onset map' })).toBeTruthy();
		expect(screen.queryByRole('button', { name: 'Open Onset map' })).toBeNull();
		expect(screen.queryByRole('button', { name: /Based on/ })).toBeNull();
	});
});
