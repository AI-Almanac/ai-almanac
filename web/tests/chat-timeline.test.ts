import { describe, expect, it } from 'vitest';

import type { ChatArtifact, ChatEvent, ChatMessage } from '../src/lib/api';
import { emptyAssistantTurn, foldTurnEvent } from '../src/lib/chat/turn';
import {
	groupSummary,
	liveLabel,
	turnTimeline,
	type ActivityGroup
} from '../src/lib/chat/timeline';

const T = 'turn-1';

function stream(events: ChatEvent[]): ChatMessage {
	return events.reduce(foldTurnEvent, emptyAssistantTurn(T));
}

function toolCall(
	id: string,
	name: string,
	status: 'pending' | 'running' | 'completed' | 'failed'
): ChatEvent {
	return {
		type: 'tool_call',
		turn_id: T,
		tool_call: { id, name, status, input: {}, artifacts: [] }
	};
}

function toolResult(id: string): ChatEvent {
	return { type: 'tool_result', turn_id: T, tool_call_id: id, status: 'completed', result: {} };
}

const figure: ChatArtifact = {
	id: 'fig-1',
	kind: 'figure',
	url: '/chat/figures/fig-1/public',
	label: 'FuXi FAR histogram',
	created_at: '2026-10-08T00:00:00Z'
};

describe('chat turn timeline', () => {
	it('shows reply text and tool work in the order they happened', () => {
		const turn = stream([
			{ type: 'text_delta', turn_id: T, content: 'Let me look.' },
			toolCall('c1', 'get_job_metrics', 'running'),
			toolResult('c1'),
			{ type: 'text_delta', turn_id: T, content: 'FuXi leads.' }
		]);

		expect(turnTimeline(turn).map((item) => item.kind)).toEqual(['text', 'activity', 'text']);
	});

	it('collapses back-to-back analyses and their progress notes into one group', () => {
		const turn = stream([
			toolCall('c1', 'run_code', 'running'),
			toolResult('c1'),
			{ type: 'thinking_delta', turn_id: T, content: 'Now the second model.' },
			toolCall('c2', 'run_code', 'running'),
			toolResult('c2'),
			toolCall('c3', 'get_job_metrics', 'running'),
			toolResult('c3')
		]);

		const items = turnTimeline(turn);
		expect(items).toHaveLength(1);
		expect(groupSummary(items[0] as ActivityGroup)).toBe('Ran custom analysis ×2 · Loaded metrics');
	});

	it('names the step in progress, including a code tool whose code is still being written', () => {
		const writing = turnTimeline(stream([toolCall('c1', 'run_code', 'pending')]));
		expect(liveLabel(writing[0] as ActivityGroup)).toBe('Writing analysis code');

		const running = turnTimeline(
			stream([toolCall('c1', 'run_code', 'pending'), toolCall('c1', 'run_code', 'running')])
		);
		expect(liveLabel(running[0] as ActivityGroup)).toBe('Running custom analysis');
		expect((running[0] as ActivityGroup).steps).toHaveLength(1);
	});

	it('carries figures a tool produced on the group so they render inline', () => {
		const turn = stream([
			toolCall('c1', 'run_code', 'running'),
			{ type: 'artifact', turn_id: T, tool_call_id: 'c1', artifact: figure },
			toolResult('c1')
		]);

		expect((turnTimeline(turn)[0] as ActivityGroup).figures.map((f) => f.id)).toEqual(['fig-1']);
	});

	it('renders turns saved before blocks existed as text followed by their tools', () => {
		const legacy: ChatMessage = {
			id: 'old',
			role: 'assistant',
			content: 'Summary.',
			created_at: '2026-10-01T00:00:00Z',
			tool_calls: [{ id: 'c1', name: 'run_code', status: 'completed', input: {}, artifacts: [] }]
		};

		expect(turnTimeline(legacy).map((item) => item.kind)).toEqual(['text', 'activity']);
	});
});
