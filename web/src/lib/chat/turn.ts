import type { ChatEvent, ChatMessage, ChatToolCall, ChatTurnBlock } from '$lib/api';

/**
 * Fold stream events into the assistant turn being streamed. Mirrors the
 * backend's `turn_events.apply_stream_event`, so the live turn matches the one
 * the `done` event persists.
 */

export function emptyAssistantTurn(id: string): ChatMessage {
	return {
		id,
		role: 'assistant',
		content: '',
		created_at: new Date().toISOString(),
		tool_calls: [],
		artifacts: [],
		guardrails: [],
		blocks: []
	};
}

function appendText(
	blocks: ChatTurnBlock[],
	kind: 'text' | 'thinking',
	text: string
): ChatTurnBlock[] {
	const last = blocks.at(-1);
	if (last?.kind === kind) return [...blocks.slice(0, -1), { kind, text: last.text + text }];
	return [...blocks, { kind, text }];
}

function upsertToolCall(turn: ChatMessage, incoming: ChatToolCall): ChatMessage {
	const toolCalls = turn.tool_calls ?? [];
	const existing = toolCalls.find((tc) => tc.id === incoming.id);
	if (!existing) {
		return {
			...turn,
			tool_calls: [...toolCalls, { ...incoming, artifacts: incoming.artifacts ?? [] }],
			blocks: [...(turn.blocks ?? []), { kind: 'tool', tool_call_id: incoming.id }]
		};
	}
	const hasInput = Object.keys(incoming.input ?? {}).length > 0;
	const advanced: ChatToolCall = {
		...existing,
		name: incoming.name,
		status: incoming.status,
		input: hasInput ? incoming.input : existing.input
	};
	return { ...turn, tool_calls: toolCalls.map((tc) => (tc.id === incoming.id ? advanced : tc)) };
}

function updateToolCall(
	turn: ChatMessage,
	id: string,
	update: (tc: ChatToolCall) => ChatToolCall
): ChatMessage {
	return {
		...turn,
		tool_calls: (turn.tool_calls ?? []).map((tc) => (tc.id === id ? update(tc) : tc))
	};
}

/** Returns the turn with the event applied; events that carry no turn content return it unchanged. */
export function foldTurnEvent(turn: ChatMessage, event: ChatEvent): ChatMessage {
	switch (event.type) {
		case 'text_delta':
			return {
				...turn,
				content: turn.content + event.content,
				blocks: appendText(turn.blocks ?? [], 'text', event.content)
			};
		case 'thinking_delta':
			return { ...turn, blocks: appendText(turn.blocks ?? [], 'thinking', event.content) };
		case 'tool_call':
			return upsertToolCall(turn, event.tool_call);
		case 'tool_result':
			return updateToolCall(turn, event.tool_call_id, (tc) => ({
				...tc,
				status: event.status,
				result: event.result
			}));
		case 'artifact':
			return updateToolCall(
				{ ...turn, artifacts: [...(turn.artifacts ?? []), event.artifact] },
				event.tool_call_id,
				(tc) => ({ ...tc, artifacts: [...(tc.artifacts ?? []), event.artifact] })
			);
		case 'guardrail':
			return {
				...turn,
				guardrails: [
					...(turn.guardrails ?? []),
					{
						tool_call_id: event.tool_call_id,
						errors: event.errors,
						warnings: event.warnings,
						finding_keys: event.finding_keys
					}
				]
			};
		default:
			return turn;
	}
}

/** Events that start a new streamed turn when none is in progress. */
export function startsTurn(event: ChatEvent): event is Extract<ChatEvent, { turn_id: string }> {
	return (
		event.type === 'text_delta' || event.type === 'thinking_delta' || event.type === 'tool_call'
	);
}
