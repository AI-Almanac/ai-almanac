import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
	BenchmarkRunSpec,
	ChatApprovalRequest,
	ChatEvent,
	ChatMessage,
	ChatScope,
	ChatSessionDetail,
	ChatToolCall,
	Job
} from '../src/lib/api';
import { approvalFromRequest } from '../src/lib/chat/approval';
import { callState } from '../src/lib/chat/timeline';
import { toolLabel } from '../src/lib/chat/tools';
import { ChatSessionState } from '../src/lib/chat/session.svelte';
import { withToolResult } from '../src/lib/chat/turn';
import { call } from './fixtures/chat';

const api = vi.hoisted(() => ({
	getChatSession: vi.fn(),
	getChatJobActivity: vi.fn(),
	sendChatMessage: vi.fn(),
	decideChatToolApproval: vi.fn()
}));

vi.mock('../src/lib/api', async () => ({
	...(await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api')),
	...api
}));

const scope: ChatScope = { kind: 'workspace', key: 'workspace-1', title: null, job_ids: [] };

function submitCall(overrides: Partial<ChatToolCall> = {}): ChatToolCall {
	return call('submit_benchmark', { id: 'call-1', status: 'running', ...overrides });
}

function askingTurn(toolCall = submitCall()): ChatMessage {
	return {
		id: 'turn-1',
		role: 'assistant',
		content: 'Here is the benchmark I would run.',
		created_at: '2026-11-10T00:00:00Z',
		tool_calls: [toolCall],
		blocks: [{ kind: 'tool', tool_call_id: toolCall.id }]
	};
}

function stepRequest(): ChatApprovalRequest {
	return {
		type: 'tool_approval_request',
		turn_id: 'turn-1',
		tool_call: submitCall(),
		metadata: {
			params: { spec: { dataset_id: 'obs-1' } },
			display: { facts: [{ label: 'Evaluation years', value: '2000–2023' }] }
		}
	};
}

function detail(overrides: Partial<ChatSessionDetail> = {}): ChatSessionDetail {
	return {
		id: 'session-1',
		title: 'Kiremt benchmark',
		created_at: '2026-11-10T00:00:00Z',
		updated_at: '2026-11-10T00:00:00Z',
		message_count: 2,
		scope,
		transcript: [askingTurn()],
		...overrides
	} as ChatSessionDetail;
}

function stream(events: ChatEvent[]) {
	return async function* () {
		yield* events;
	};
}

function doneEvent(turn: ChatMessage): ChatEvent {
	return { type: 'done', turn };
}

/** A step's label as a live conversation shows it. */
function label(
	toolCall: Parameters<typeof callState>[0] & Parameters<typeof toolLabel>[0]
): string {
	return toolLabel(toolCall, callState(toolCall, true));
}

function stepLabels(chat: ChatSessionState): string[] {
	return chat.visibleTurns.flatMap((turn) => (turn.tool_calls ?? []).map(label));
}

async function reloadedChat(pending: ChatApprovalRequest | null = stepRequest()) {
	api.getChatSession.mockResolvedValue(detail({ pending_approval: pending }));
	const chat = new ChatSessionState(() => scope);
	await chat.loadSession('session-1');
	return chat;
}

async function liveChat() {
	api.getChatSession.mockResolvedValue(detail({ transcript: [] }));
	api.sendChatMessage.mockImplementation(
		stream([
			{ type: 'tool_call', turn_id: 'turn-1', tool_call: submitCall() },
			stepRequest(),
			doneEvent(askingTurn())
		])
	);
	const chat = new ChatSessionState(() => scope);
	await chat.loadSession('session-1');
	await chat.submit('Run the benchmark.');
	return chat;
}

beforeEach(() => {
	vi.clearAllMocks();
	api.getChatJobActivity.mockResolvedValue({ events: [], active: 0 });
	api.decideChatToolApproval.mockImplementation(stream([]));
});

describe('a run waiting for approval', () => {
	it('shows its card again when the conversation is loaded', async () => {
		const chat = await reloadedChat();

		expect(chat.pendingApproval).toEqual(approvalFromRequest(stepRequest()));
		expect(chat.pendingApproval).toMatchObject({
			kind: 'step',
			toolName: 'submit_benchmark',
			facts: [{ label: 'Evaluation years', value: '2000–2023' }]
		});
	});

	it('asks the same thing after a reload as it did live', async () => {
		const live = await liveChat();
		const reloaded = await reloadedChat();

		expect(reloaded.pendingApproval).toEqual(live.pendingApproval);
	});

	it('reads as waiting, not submitted, live and after a reload', async () => {
		expect(stepLabels(await liveChat())).toEqual(['Waiting for your approval']);
		expect(stepLabels(await reloadedChat())).toEqual(['Waiting for your approval']);
	});

	it('is approved after a reload exactly as it is live', async () => {
		for (const chat of [await liveChat(), await reloadedChat()]) {
			api.decideChatToolApproval.mockClear();
			await chat.approveSubmit();

			expect(api.decideChatToolApproval.mock.calls).toEqual([
				['session-1', { tool_call_id: 'call-1', approved: true }]
			]);
			expect(chat.pendingApproval).toBeNull();
		}
	});

	it('reads as submitted once the approved run reports back', async () => {
		const chat = await reloadedChat();
		api.decideChatToolApproval.mockImplementation(
			stream([
				{
					type: 'tool_result',
					turn_id: 'turn-2',
					tool_call_id: 'call-1',
					status: 'completed',
					result: { run_id: 'run-1' }
				},
				doneEvent({ id: 'turn-2', role: 'assistant', content: 'It is running.', created_at: '' })
			])
		);

		await chat.approveSubmit();

		expect(stepLabels(chat)).toEqual(['Submitted the benchmark']);
	});

	it('reads as not run after "Not yet", whatever the assistant is told about it', async () => {
		const chat = await reloadedChat();
		api.decideChatToolApproval.mockImplementation(
			stream([
				{
					type: 'tool_result',
					turn_id: 'turn-2',
					tool_call_id: 'call-1',
					status: 'completed',
					result: { raw: 'The user declined this step.' }
				},
				doneEvent({ id: 'turn-2', role: 'assistant', content: 'No problem.', created_at: '' })
			])
		);

		await chat.declineSubmit();

		expect(api.decideChatToolApproval.mock.calls).toEqual([
			[
				'session-1',
				{ tool_call_id: 'call-1', approved: false, message: 'The user declined this step.' }
			]
		]);
		expect(chat.pendingApproval).toBeNull();
		expect(stepLabels(chat)).toEqual(['Benchmark not submitted']);
	});

	it('reads as not run when the user writes a new message instead of answering', async () => {
		const chat = await reloadedChat();
		api.sendChatMessage.mockImplementation(stream([]));

		await chat.submit('Use 2005 onwards instead.');

		expect(chat.pendingApproval).toBeNull();
		expect(stepLabels(chat)).toEqual(['Benchmark not submitted']);
	});

	it('is gone on switching to a conversation that is not waiting on anything', async () => {
		const chat = await reloadedChat();
		api.getChatSession.mockResolvedValue(detail({ id: 'session-2', transcript: [] }));

		await chat.loadSession('session-2');

		expect(chat.pendingApproval).toBeNull();
	});

	it('restores a benchmark drafted in a page chat and approves it like any other step', async () => {
		const config = { model_names: ['AIFS'], region_name: 'Ethiopia' } as BenchmarkRunSpec;
		const jobs = [{ id: 'job-1' }] as Job[];
		const onBenchmarkConfig = vi.fn();
		const onBenchmarkSubmitted = vi.fn();
		api.getChatSession.mockResolvedValue(
			detail({
				transcript: [
					askingTurn(submitCall({ status: 'completed', result: { approval_required: true } }))
				],
				pending_approval: {
					type: 'benchmark_approval_request',
					turn_id: 'turn-1',
					tool_call_id: 'call-1',
					config
				} as ChatApprovalRequest
			})
		);
		api.decideChatToolApproval.mockImplementation(
			stream([
				{
					type: 'tool_result',
					turn_id: 'turn-2',
					tool_call_id: 'call-1',
					status: 'completed',
					result: { run_id: 'run-1' }
				},
				{ type: 'benchmark_config', turn_id: 'turn-2', config, run_id: 'run-1', jobs },
				doneEvent({ id: 'turn-2', role: 'assistant', content: 'It is running.', created_at: '' })
			])
		);
		const chat = new ChatSessionState(() => scope, { onBenchmarkConfig, onBenchmarkSubmitted });
		await chat.loadSession('session-1');

		expect(onBenchmarkConfig).toHaveBeenLastCalledWith(config, null);
		expect(stepLabels(chat)).toEqual(['Waiting for your approval']);

		await chat.approveSubmit();

		expect(api.decideChatToolApproval.mock.calls).toEqual([
			['session-1', { tool_call_id: 'call-1', approved: true }]
		]);
		expect(onBenchmarkSubmitted).toHaveBeenCalledWith('run-1', jobs, 'session-1');
		expect(stepLabels(chat)).toEqual(['Submitted the benchmark']);
	});
});

describe('step labels', () => {
	it.each(['submit_benchmark', 'submit_blend', 'rerun_job', 'run_forecast', 'update_forecast'])(
		'%s says it is waiting for approval instead of that it ran',
		(name) => {
			for (const status of ['running', 'completed'] as const) {
				expect(label({ name, status, awaiting_approval: true })).toBe('Waiting for your approval');
			}
		}
	);

	it('says a declined step did not run, in words that fit the step', () => {
		const declined = { status: 'failed' as const, result: { error: 'Not approved' } };

		expect(label({ name: 'submit_blend', ...declined })).toBe('Blend not submitted');
		expect(label({ name: 'run_forecast', ...declined })).toBe('Forecast not run');
		expect(label({ name: 'some_new_step', ...declined })).toBe('Step not run');
		// The step summary reads every step as settled; a declined one must still say so.
		expect(label({ name: 'submit_blend', ...declined, status: 'completed' })).toBe(
			'Blend not submitted'
		);
	});

	it('still reports an ordinary failure as the step it was', () => {
		const failed = { status: 'failed' as const, result: { error: 'No such dataset' } };

		expect(label({ name: 'submit_benchmark', ...failed })).toBe('Submitted the benchmark');
	});

	it.each([
		['cancel_run', 'running', 'Canceling the run'],
		['cancel_run', 'completed', 'Canceled the run'],
		['summarize_skill_by_area', 'running', 'Averaging skill over each area'],
		['summarize_skill_by_area', 'completed', 'Averaged skill over each area']
	] as const)('names %s while %s as "%s"', (name, status, words) => {
		expect(label({ name, status })).toBe(words);
	});

	it.each(['plot_map', 'inspect_artifact', 'run_forecast'])(
		'names %s in plain words rather than by tool name',
		(name) => {
			expect(label({ name, status: 'completed' })).not.toContain('_');
		}
	);
});

describe('withToolResult', () => {
	it('records an outcome on the call that asked for it and leaves the others alone', () => {
		const other = submitCall({ id: 'call-2', name: 'list_models', status: 'completed' });
		const turns = [{ ...askingTurn(), tool_calls: [submitCall(), other] }];

		const [turn] = withToolResult(turns, 'call-1', { run_id: 'run-1' });

		expect(turn.tool_calls).toEqual([
			submitCall({ status: 'completed', result: { run_id: 'run-1' } }),
			other
		]);
	});
});
