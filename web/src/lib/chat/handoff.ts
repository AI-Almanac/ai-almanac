import type { ChatMessage } from '$lib/api';

export type SetupHandoff = {
	workflow: 'benchmark' | 'blend';
	request: string;
	href: string;
};

const SETUP_PAGES: Record<SetupHandoff['workflow'], string> = {
	benchmark: '/benchmarks',
	blend: '/blends'
};

function isWorkflow(value: unknown): value is SetupHandoff['workflow'] {
	return typeof value === 'string' && Object.hasOwn(SETUP_PAGES, value);
}

/** The setup link an `open_setup` tool result offers, or null for anything else. */
export function parseSetupHandoff(result: unknown): SetupHandoff | null {
	const handoff = (result as { setup_handoff?: unknown } | null)?.setup_handoff;
	if (!handoff || typeof handoff !== 'object') return null;
	const { workflow, request } = handoff as Record<string, unknown>;
	if (!isWorkflow(workflow) || typeof request !== 'string' || !request.trim()) return null;
	const query = new URLSearchParams({ q: request.trim() });
	return { workflow, request: request.trim(), href: `${SETUP_PAGES[workflow]}?${query}` };
}

/** Read from the turn's tool calls, so the links survive a reload like the rest of the turn. */
export function setupHandoffs(turn: ChatMessage): SetupHandoff[] {
	return (turn.tool_calls ?? [])
		.filter((call) => call.name === 'open_setup' && call.status === 'completed')
		.map((call) => parseSetupHandoff(call.result))
		.filter((handoff): handoff is SetupHandoff => handoff !== null);
}
