import type { ChatArtifact, ChatMessage, ChatToolCall, ChatTurnBlock } from '$lib/api';
import { toolLabel } from '$lib/chat/format';

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

/** Turns saved before blocks were recorded rendered their text above their tools. */
function legacyBlocks(turn: ChatMessage): ChatTurnBlock[] {
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
 * The turn as the user reads it: reply text interleaved with activity groups,
 * where consecutive tool calls and progress notes collapse into one group.
 */
export function turnTimeline(turn: ChatMessage): TimelineItem[] {
	const blocks = turn.blocks?.length ? turn.blocks : legacyBlocks(turn);
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

export function isStepActive(step: ActivityStep): boolean {
	return (
		step.kind === 'tool' &&
		(step.toolCall.status === 'pending' || step.toolCall.status === 'running')
	);
}

export function groupFailed(group: ActivityGroup): boolean {
	return group.steps.some((step) => step.kind === 'tool' && step.toolCall.status === 'failed');
}

/** The live header: what the assistant is doing right now. */
export function liveLabel(group: ActivityGroup): string {
	const last = group.steps.at(-1);
	if (last?.kind === 'tool' && isStepActive(last)) return toolLabel(last.toolCall);
	return 'Working';
}

/** The settled header: what the group did, e.g. "Ran custom analysis ×3 · Loaded metrics". */
export function groupSummary(group: ActivityGroup): string {
	const counts = new Map<string, number>();
	for (const step of group.steps) {
		if (step.kind !== 'tool') continue;
		const label = toolLabel({ ...step.toolCall, status: 'completed' });
		counts.set(label, (counts.get(label) ?? 0) + 1);
	}
	if (counts.size === 0) return 'Thought it through';
	return [...counts].map(([label, n]) => (n > 1 ? `${label} ×${n}` : label)).join(' · ');
}
