import type { ChatArtifact, ChatMessage, ChatToolCall, ChatTurnBlock } from '$lib/api';
import { wasDeclined } from '$lib/chat/approval';
import { toolLabel } from './tools';

export type ActivityStep =
	{ kind: 'thinking'; text: string } | { kind: 'tool'; toolCall: ChatToolCall };

/** A run of work between two pieces of the reply, shown as one collapsible row. */
export type ActivityGroup = {
	kind: 'activity';
	key: string;
	steps: ActivityStep[];
	figures: ChatArtifact[];
};

export type TimelineItem = { kind: 'text'; key: string; text: string } | ActivityGroup;

type TurnContent = Pick<ChatMessage, 'content' | 'tool_calls' | 'blocks'>;

/** Turns saved before blocks were recorded rendered their text above their tools. */
function legacyBlocks(turn: TurnContent): ChatTurnBlock[] {
	return [
		...(turn.content ? [{ kind: 'text', text: turn.content } as const] : []),
		...(turn.tool_calls ?? []).map((tc) => ({ kind: 'tool', tool_call_id: tc.id }) as const)
	];
}

function stepFor(block: ChatTurnBlock, toolCalls: Map<string, ChatToolCall>): ActivityStep | null {
	if (block.kind === 'thinking') return block.text.trim() ? block : null;
	if (block.kind !== 'tool') return null;
	const toolCall = toolCalls.get(block.tool_call_id);
	return toolCall ? { kind: 'tool', toolCall } : null;
}

/**
 * The turn's blocks in the order they happened. A turn saved with its steps but none
 * of its reply (a fault in how finished turns were saved, since fixed) gets the reply
 * back after its steps, which is where a reply to finished work reads.
 */
function shownBlocks(turn: TurnContent): ChatTurnBlock[] {
	if (!turn.blocks?.length) return legacyBlocks(turn);
	const replyLost = turn.content.trim() && !turn.blocks.some((block) => block.kind === 'text');
	return replyLost ? [...turn.blocks, { kind: 'text', text: turn.content }] : turn.blocks;
}

/**
 * The turn as the user reads it: reply text interleaved with activity groups,
 * where consecutive tool calls and progress notes collapse into one group.
 */
export function turnTimeline(turn: TurnContent): TimelineItem[] {
	const blocks = shownBlocks(turn);
	const toolCalls = new Map((turn.tool_calls ?? []).map((tc) => [tc.id, tc]));
	const items: TimelineItem[] = [];
	blocks.forEach((block, index) => {
		if (block.kind === 'text') {
			// Whitespace between two tool calls should not split their group.
			if (block.text.trim()) items.push({ kind: 'text', key: `text-${index}`, text: block.text });
			return;
		}
		const step = stepFor(block, toolCalls);
		if (!step) return;
		const last = items.at(-1);
		const group: ActivityGroup =
			last?.kind === 'activity'
				? last
				: { kind: 'activity', key: `activity-${index}`, steps: [], figures: [] };
		if (group !== last) items.push(group);
		group.steps.push(step);
		if (step.kind === 'tool') group.figures.push(...(step.toolCall.artifacts ?? []));
	});
	return items;
}

function isUnderWay(call: Pick<ChatToolCall, 'status'>): boolean {
	return call.status === 'pending' || call.status === 'running';
}

/**
 * Where a step stands. `waiting` is a step the user has yet to approve and
 * `declined` one they chose not to run: neither went wrong, and neither was done.
 */
export type StepState = 'active' | 'waiting' | 'declined' | 'failed' | 'done';

export function callState(
	call: Pick<ChatToolCall, 'status' | 'result' | 'awaiting_approval'>,
	live: boolean
): StepState {
	if (call.awaiting_approval) return 'waiting';
	if (wasDeclined(call)) return 'declined';
	if (call.status === 'failed') return 'failed';
	return live && isUnderWay(call) ? 'active' : 'done';
}

export function stepState(step: ActivityStep, live: boolean): StepState {
	return step.kind === 'tool' ? callState(step.toolCall, live) : 'done';
}

/** The group as a whole: a step waiting on the user comes first, then work under way, then what went wrong. */
export function groupState(group: ActivityGroup, live: boolean): StepState {
	const states = new Set(group.steps.map((step) => stepState(step, live)));
	if (states.has('waiting')) return 'waiting';
	if (live) return 'active';
	return (['failed', 'declined'] as const).find((state) => states.has(state)) ?? 'done';
}

/** The live header: what the assistant is doing right now. */
export function liveLabel(group: ActivityGroup): string {
	const last = group.steps.at(-1);
	if (last?.kind === 'tool' && isUnderWay(last.toolCall)) return toolLabel(last.toolCall, 'active');
	return 'Working';
}

/** The settled header: what the group did, e.g. "Ran custom analysis ×3 · Loaded metrics". */
export function groupSummary(group: ActivityGroup): string {
	const counts = new Map<string, number>();
	for (const step of group.steps) {
		if (step.kind !== 'tool') continue;
		const label = toolLabel(step.toolCall, callState(step.toolCall, false));
		counts.set(label, (counts.get(label) ?? 0) + 1);
	}
	if (counts.size === 0) return 'Thought it through';
	return [...counts].map(([label, n]) => (n > 1 ? `${label} ×${n}` : label)).join(' · ');
}
