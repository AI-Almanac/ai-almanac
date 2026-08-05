// ---- Assistant rulesets (admin) ---------------------------------------------
//
// A ruleset is the assistant's *wording*: which prompt sections it gets, in what
// order, and which tools it is withheld. It never decides what the platform
// accepts — the statistical guardrails do that server-side, past the model — so
// editing one here cannot loosen a configuration rule.
import { authHeaders } from '../auth';
import { BASE_URL, request } from './core';
import { sseEvents, type ChatEvent, type ChatScope } from './chat';

export type PromptSection = {
	key: string;
	title: string;
	body: string;
	/** A required section cannot be disabled, so an edit can't drop the caveats. */
	required: boolean;
	enabled: boolean;
	/** Empty means the section applies to every scope kind. */
	scope_kinds: string[];
};

export type ToolPolicy = {
	deny: string[];
};

export type RulesetSummary = {
	id: string;
	name: string;
	description: string;
	version: number;
	source: 'packaged' | 'custom';
	is_active: boolean;
	/** Whether users can see this ruleset: in the style picker and as a comparison arm. */
	comparison_enabled: boolean;
	section_keys: string[];
	denied_tools: string[];
	model: string | null;
};

export type RulesetDetail = {
	id: string;
	name: string;
	description: string;
	version: number;
	source: 'packaged' | 'custom';
	is_active: boolean;
	prompt_sections: PromptSection[];
	tool_policy: ToolPolicy;
	model: string | null;
	model_settings: Record<string, unknown> | null;
};

/** The enforced thresholds. Read-only here: they are a platform setting. */
export type GuardrailThresholds = {
	min_onset_years: number;
	min_training_years: number;
	blend_member_warn: number;
	small_sample_years: number;
	presatellite_end_year: number;
};

export type PromptPreview = {
	scope_kind: string;
	instructions: string;
	character_count: number;
};

export const PREVIEW_SCOPE_KINDS = [
	'benchmark_setup',
	'benchmark_run_group',
	'blend_setup',
	'job_set'
] as const;

export async function listRulesets(): Promise<RulesetSummary[]> {
	return request<RulesetSummary[]>('/assistant/rulesets');
}

export async function getRuleset(id: string): Promise<RulesetDetail> {
	return request<RulesetDetail>(`/assistant/rulesets/${encodeURIComponent(id)}`);
}

export async function getGuardrailThresholds(): Promise<GuardrailThresholds> {
	return request<GuardrailThresholds>('/assistant/guardrails');
}

export async function saveRuleset(detail: RulesetDetail): Promise<RulesetDetail> {
	return request<RulesetDetail>(`/assistant/rulesets/${encodeURIComponent(detail.id)}`, {
		method: 'PUT',
		body: JSON.stringify({
			id: detail.id,
			name: detail.name,
			description: detail.description,
			version: detail.version,
			prompt_sections: detail.prompt_sections,
			tool_policy: detail.tool_policy,
			model: detail.model,
			model_settings: detail.model_settings
		})
	});
}

/** Copy to a new id, one version up, so logged turns keep the wording that produced them. */
export async function cloneRuleset(
	sourceId: string,
	id: string,
	name: string
): Promise<RulesetDetail> {
	return request<RulesetDetail>(`/assistant/rulesets/${encodeURIComponent(sourceId)}/clone`, {
		method: 'POST',
		body: JSON.stringify({ id, name, prompt_sections: [] })
	});
}

export async function activateRuleset(id: string): Promise<RulesetSummary> {
	return request<RulesetSummary>(`/assistant/rulesets/${encodeURIComponent(id)}/activate`, {
		method: 'POST'
	});
}

/** Expose or hide a ruleset for users (style picker and comparison arms). */
export async function setRulesetComparisonEnabled(
	id: string,
	enabled: boolean
): Promise<RulesetSummary> {
	return request<RulesetSummary>(
		`/assistant/rulesets/${encodeURIComponent(id)}/comparison-enabled`,
		{ method: 'POST', body: JSON.stringify({ enabled }) }
	);
}

/** Archive a custom ruleset. Packaged and active rulesets are refused (409). */
export async function deleteRuleset(id: string): Promise<void> {
	await request<void>(`/assistant/rulesets/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function previewRuleset(id: string, scopeKind: string): Promise<PromptPreview> {
	return request<PromptPreview>(`/assistant/rulesets/${encodeURIComponent(id)}/preview`, {
		method: 'POST',
		body: JSON.stringify({ scope_kind: scopeKind })
	});
}

// ---- Side-by-side comparison -------------------------------------------------
//
// Two answers to one message, produced under two rulesets (or two models under
// one ruleset), so a wording change can be judged on evidence. Each arm runs in
// its own scratch session and neither can submit anything: the submit tools are
// withheld server-side, at registration.

export type ComparisonVariantSpec = {
	ruleset_id: string;
	model?: string | null;
};

export type ComparisonVariant = {
	variant: number;
	session_id: string;
	// Absent on a blind comparison: the arms stay anonymous until the vote.
	ruleset_id?: string;
	ruleset_name?: string;
	ruleset_version?: number;
	model?: string | null;
};

/** One arm's identity, disclosed by the vote response. */
export type RevealedArm = {
	session_id: string;
	ruleset_id: string | null;
	ruleset_name: string | null;
	ruleset_version: number | null;
};

export type VoteResult = {
	rated_turns: number;
	arms: RevealedArm[];
};

/** What a non-admin may know about a ruleset: enough to pick one. */
export type RulesetOption = {
	id: string;
	name: string;
	description: string;
	is_active: boolean;
};

export type RulesetOptions = {
	rulesets: RulesetOption[];
	/** The comparison feature flag: off hides the whole surface. */
	comparisons_enabled: boolean;
	/** Flag on *and* two rulesets exposed, so a comparison can actually run. */
	compare_available: boolean;
};

export type RulesetFeedback = {
	ruleset_id: string;
	ruleset_version: number | null;
	turns: number;
	rated: number;
	wins: number;
	losses: number;
	ties: number;
	flag_counts: Record<string, number>;
};

export async function getRulesetOptions(): Promise<RulesetOptions> {
	return request<RulesetOptions>('/assistant/ruleset-options');
}

export async function getRulesetFeedback(): Promise<RulesetFeedback[]> {
	return request<RulesetFeedback[]>('/assistant/feedback');
}

export type CompareEvent =
	| { type: 'comparison_started'; comparison_id: string; variants: ComparisonVariant[] }
	| { type: 'comparison_complete'; comparison_id: string }
	| (ChatEvent & { variant: number });

export async function* compareRulesets(
	message: string,
	variants: ComparisonVariantSpec[],
	options: { sourceSessionId?: string; scope?: ChatScope } = {}
): AsyncGenerator<CompareEvent> {
	const res = await fetch(`${BASE_URL}/assistant/compare`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', ...authHeaders() },
		body: JSON.stringify({
			message,
			variants,
			source_session_id: options.sourceSessionId ?? null,
			scope: options.scope ?? null
		})
	});
	if (!res.ok) {
		throw new Error(`Comparison failed (${res.status}): ${await res.text()}`);
	}
	for await (const event of sseEvents<CompareEvent>(res)) {
		yield event;
		if (event.type === 'comparison_complete') return;
	}
}

/**
 * A blind comparison of two user-chosen rulesets. The server shuffles which
 * column is which, so the stream never says — voting is what reveals them.
 */
export async function* blindCompare(
	message: string,
	rulesetIds: [string, string],
	options: { sourceSessionId?: string; scope?: ChatScope } = {}
): AsyncGenerator<CompareEvent> {
	const res = await fetch(`${BASE_URL}/assistant/compare/blind`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', ...authHeaders() },
		body: JSON.stringify({
			message,
			ruleset_ids: rulesetIds,
			source_session_id: options.sourceSessionId ?? null,
			scope: options.scope ?? null
		})
	});
	if (!res.ok) {
		throw new Error(`Comparison failed (${res.status}): ${await res.text()}`);
	}
	for await (const event of sseEvents<CompareEvent>(res)) {
		yield event;
		if (event.type === 'comparison_complete') return;
	}
}

/**
 * Continue a live comparison: the follow-up runs through both arms' scratch
 * conversations under their original rulesets. Arm identity is never in the
 * stream — a labeled client already knows it, a blind one must not learn it.
 */
export async function* continueComparison(
	comparisonId: string,
	message: string
): AsyncGenerator<CompareEvent> {
	const res = await fetch(
		`${BASE_URL}/assistant/comparisons/${encodeURIComponent(comparisonId)}/message`,
		{
			method: 'POST',
			headers: { 'Content-Type': 'application/json', ...authHeaders() },
			body: JSON.stringify({ message })
		}
	);
	if (!res.ok) {
		throw new Error(`Comparison follow-up failed (${res.status}): ${await res.text()}`);
	}
	for await (const event of sseEvents<CompareEvent>(res)) {
		yield event;
		if (event.type === 'comparison_complete') return;
	}
}

/** Record which arm won. `null` is a tie; the vote lands on both turn logs. */
export async function voteOnComparison(
	comparisonId: string,
	winnerSessionId: string | null,
	note?: string
): Promise<VoteResult> {
	return request<VoteResult>(`/assistant/comparisons/${encodeURIComponent(comparisonId)}/vote`, {
		method: 'POST',
		body: JSON.stringify({ winner_session_id: winnerSessionId, note: note ?? null })
	});
}

export async function discardComparison(comparisonId: string): Promise<void> {
	await request<void>(`/assistant/comparisons/${encodeURIComponent(comparisonId)}`, {
		method: 'DELETE'
	});
}
