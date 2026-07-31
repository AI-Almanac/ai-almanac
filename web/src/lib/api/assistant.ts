// ---- Assistant rulesets (admin) ---------------------------------------------
//
// A ruleset is the assistant's *wording*: which prompt sections it gets, in what
// order, and which tools it is withheld. It never decides what the platform
// accepts — the statistical guardrails do that server-side, past the model — so
// editing one here cannot loosen a configuration rule.
import { request } from './core';

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

export async function previewRuleset(id: string, scopeKind: string): Promise<PromptPreview> {
	return request<PromptPreview>(`/assistant/rulesets/${encodeURIComponent(id)}/preview`, {
		method: 'POST',
		body: JSON.stringify({ scope_kind: scopeKind })
	});
}
