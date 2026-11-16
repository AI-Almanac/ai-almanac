import type {
	BenchmarkRunSpec,
	BenchmarkValidation,
	BlendRunSpec,
	BlendValidation,
	ChatApprovalRequest,
	ChatEvent,
	ChatMessage,
	ChatToolCall
} from '$lib/api';
import { approvalCopy, type ApprovalCopy } from './tools';
import { resultError } from './tool-results';

/** One line of a workspace approval card, in the user's words (e.g. "Models: AIFS, FuXi"). */
export type ApprovalFact = { label: string; value: string };

function isFact(value: unknown): value is ApprovalFact {
	if (typeof value !== 'object' || value === null) return false;
	const { label, value: text } = value as Record<string, unknown>;
	return typeof label === 'string' && typeof text === 'string';
}

/** The facts in an approval's server-written `display`; malformed entries are left out. */
function approvalFacts(display: unknown): ApprovalFact[] {
	const facts = (display as { facts?: unknown } | null | undefined)?.facts;
	return Array.isArray(facts) ? facts.filter(isFact) : [];
}

/** A run the assistant has asked to start and the user has not yet answered. */
export type PendingApproval =
	| {
			kind: 'benchmark';
			toolCallId: string;
			config: BenchmarkRunSpec;
			validation: BenchmarkValidation | null;
	  }
	| {
			kind: 'blend';
			toolCallId: string;
			config: BlendRunSpec;
			validation: BlendValidation | null;
	  }
	| {
			kind: 'step';
			toolCallId: string;
			toolName: string;
			params: Record<string, unknown>;
			facts: ApprovalFact[];
			validation: null;
	  };

const APPROVAL_REQUESTS = new Set<ChatEvent['type']>([
	'tool_approval_request',
	'benchmark_approval_request',
	'blend_approval_request'
]);

export function isApprovalRequest(event: ChatEvent): event is ChatApprovalRequest {
	return APPROVAL_REQUESTS.has(event.type);
}

/**
 * The card an approval request asks for. The request reads the same whether it
 * arrived on the stream or with a conversation that was loaded while it waited.
 */
export function approvalFromRequest(request: ChatApprovalRequest): PendingApproval {
	switch (request.type) {
		case 'tool_approval_request':
			return {
				kind: 'step',
				toolCallId: request.tool_call.id,
				toolName: request.tool_call.name,
				params: (request.metadata?.params as Record<string, unknown>) ?? {},
				facts: approvalFacts(request.metadata?.display),
				validation: null
			};
		case 'benchmark_approval_request':
			return {
				kind: 'benchmark',
				toolCallId: request.tool_call_id,
				config: request.config,
				validation: request.validation ?? null
			};
		case 'blend_approval_request':
			return {
				kind: 'blend',
				toolCallId: request.tool_call_id,
				config: request.config,
				validation: request.validation ?? null
			};
	}
}

/** What the server records on a step the user chose not to run. */
export const NOT_APPROVED = { error: 'Not approved' } as const;

export function wasDeclined(toolCall: Pick<ChatToolCall, 'result'>): boolean {
	return resultError(toolCall.result) === NOT_APPROVED.error;
}

function approvedTool(approval: PendingApproval): string {
	if (approval.kind === 'step') return approval.toolName;
	return approval.kind === 'benchmark' ? 'submit_benchmark' : 'submit_blend';
}

/** What the approval card says and what its go-ahead button does. */
export function approvalCardCopy(approval: PendingApproval): ApprovalCopy {
	return approvalCopy(approvedTool(approval));
}

/**
 * The page-chat cards' one-line summary, leaving out what the plan does not name;
 * workspace steps list their facts instead.
 */
export function approvalSummary(approval: PendingApproval): string | undefined {
	if (approval.kind === 'step') return undefined;
	const models = approval.config.model_names?.join(', ') || 'Selected models';
	const parts =
		approval.kind === 'benchmark'
			? [
					models,
					approval.config.region_name,
					`Days 1–${approval.config.forecast_window_days ?? 30}`
				]
			: [
					models,
					approval.config.obs_dataset_name ?? 'Selected observations',
					`Train ${approval.config.training_years || '—'}`
				];
	return parts.filter(Boolean).join(' · ');
}

/** The turns with the step the user has yet to decide on marked as waiting. */
export function withAwaitingApproval(
	turns: ChatMessage[],
	toolCallId: string | null | undefined
): ChatMessage[] {
	if (!toolCallId) return turns;
	return turns.map((turn) =>
		turn.tool_calls?.some((call) => call.id === toolCallId)
			? {
					...turn,
					tool_calls: turn.tool_calls.map((call) =>
						call.id === toolCallId ? { ...call, awaiting_approval: true } : call
					)
				}
			: turn
	);
}
