import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import ChatMessages from '../src/lib/components/ChatMessages.svelte';
import type { ChatMessage, ChatToolCall, ChatTurnBlock } from '../src/lib/api';
import type { ChatSessionState } from '../src/lib/chat/session.svelte';

import { call } from './fixtures/chat';

vi.mock('../src/lib/components/FigureCard.svelte', async () => ({
	default: (await import('./fixtures/FigureCardStub.svelte')).default
}));

function chatWith(turns: ChatMessage[], streaming: ChatMessage | null = null): ChatSessionState {
	return {
		sessionId: 'session-1',
		messages: turns,
		streamingTurn: streaming,
		visibleTurns: streaming ? [...turns, streaming] : turns,
		sending: streaming !== null,
		loadingSession: false,
		pendingApproval: null,
		jobActivity: { events: [], active: 0 }
	} as unknown as ChatSessionState;
}

function assistant(
	content: string,
	tool_calls: ChatToolCall[],
	blocks?: ChatTurnBlock[]
): ChatMessage {
	return { id: 'turn-1', role: 'assistant', content, created_at: '', tool_calls, blocks };
}

function toolBlock(toolCall: ChatToolCall): ChatTurnBlock {
	return { kind: 'tool', tool_call_id: toolCall.id };
}

function renderMessages(chat: ChatSessionState, extra: Record<string, unknown> = {}) {
	return render(ChatMessages, {
		chat,
		emptyMessage: 'Empty',
		suggestions: [],
		onSuggestion: vi.fn(),
		onOpenArtifact: vi.fn(),
		...extra
	});
}

describe('ChatMessages steps', () => {
	it('shows each step where it happened in the reply', () => {
		const check = call('validate_benchmark_config');
		renderMessages(
			chatWith([
				assistant(
					'Checking your setup.\n\nAll good.',
					[check],
					[
						{ kind: 'text', text: 'Checking your setup.' },
						toolBlock(check),
						{ kind: 'text', text: 'All good.' }
					]
				)
			])
		);

		const text = document.querySelector('.messages')!.textContent!;
		expect(text.indexOf('Checking your setup.')).toBeLessThan(
			text.indexOf('Checked the benchmark plan')
		);
		expect(text.indexOf('Checked the benchmark plan')).toBeLessThan(text.indexOf('All good.'));
	});

	it('folds consecutive steps into one expandable step', () => {
		const steps = [call('list_models'), call('list_regions')];
		renderMessages(chatWith([assistant('', steps, steps.map(toolBlock))]));

		expect(screen.getByText('Checked models · Checked regions')).toBeTruthy();
	});

	it('shows a running step while the reply streams', () => {
		const metrics = call('get_job_metrics', { status: 'running' });
		renderMessages(chatWith([], assistant('', [metrics], [toolBlock(metrics)])));

		expect(screen.getByText('Loading metrics…')).toBeTruthy();
	});

	it('says a run is waiting for approval rather than submitted', () => {
		const submit = call('submit_benchmark', { status: 'running', awaiting_approval: true });
		renderMessages(chatWith([assistant('', [submit], [toolBlock(submit)])]));

		expect(screen.getByText('Waiting for your approval')).toBeTruthy();
		expect(screen.queryByText(/Submitt(ed|ing) the benchmark/)).toBeNull();
	});

	it('says a run the user declined was not submitted, not that it failed', () => {
		const submit = call('submit_benchmark', {
			status: 'failed',
			result: { error: 'Not approved' }
		});
		renderMessages(chatWith([assistant('', [submit], [toolBlock(submit)])]));

		expect(screen.getByText('Benchmark not submitted')).toBeTruthy();
		expect(screen.queryByText('Not approved')).toBeNull();
	});

	it('offers the results once every submitted run completes', async () => {
		const onOpenItem = vi.fn();
		const submit = call('submit_benchmark', {
			result: { jobs: [{ id: 'job-1', model_display_name: 'FuXi', status: 'queued' }] }
		});
		const { rerender } = renderMessages(chatWith([assistant('Submitted.', [submit])]), {
			onOpenItem,
			jobStatuses: { 'job-1': 'running' }
		});

		expect(screen.getByText('Running')).toBeTruthy();
		expect(screen.queryByText('View results')).toBeNull();

		await rerender({ jobStatuses: { 'job-1': 'complete' } });
		await fireEvent.click(screen.getByText('View results'));
		expect(onOpenItem).toHaveBeenCalledWith({ kind: 'run', jobIds: ['job-1'] });
	});

	it('hides the results button when there is nowhere to open them', () => {
		const submit = call('submit_benchmark', {
			result: { jobs: [{ id: 'job-1', model_display_name: 'FuXi', status: 'complete' }] }
		});
		renderMessages(chatWith([assistant('Submitted.', [submit])]));

		expect(screen.getByText('Complete')).toBeTruthy();
		expect(screen.queryByText('View results')).toBeNull();
	});

	it('opens artifacts linked from the reply', async () => {
		const onOpenItem = vi.fn();
		renderMessages(chatWith([assistant('See [the onset map](artifact:onset-map).', [])]), {
			onOpenItem
		});

		await fireEvent.click(screen.getByText('the onset map'));
		expect(onOpenItem).toHaveBeenCalledWith({ kind: 'artifact', ref: 'onset-map' });
	});

	it('shows figures under the step that made them', async () => {
		const onOpenArtifact = vi.fn();
		const artifact = {
			id: 'art-1',
			kind: 'figure' as const,
			url: '/figure.webp',
			label: 'Onset map',
			created_at: ''
		};
		renderMessages(chatWith([assistant('', [call('plot_map', { artifacts: [artifact] })])]), {
			onOpenArtifact
		});

		await fireEvent.click(screen.getByText('Onset map'));
		expect(onOpenArtifact).toHaveBeenCalledWith(artifact);
	});
});
