import type { ChatMessage, GuardrailNotice } from '$lib/api';

/**
 * The guardrail notices still worth showing, by turn id. Each validation
 * describes the whole config as it stood, so only the latest notice per config
 * kind is current; a clean latest validation shows nothing.
 */
export function currentGuardrails(turns: ChatMessage[]): Map<string, GuardrailNotice[]> {
	const latest = new Map<string, { turnId: string; notice: GuardrailNotice }>();
	for (const turn of turns) {
		for (const notice of turn.guardrails ?? []) {
			latest.set(notice.config_kind ?? 'config', { turnId: turn.id, notice });
		}
	}
	const byTurn = new Map<string, GuardrailNotice[]>();
	for (const { turnId, notice } of latest.values()) {
		if (!notice.errors.length && !notice.warnings.length) continue;
		byTurn.set(turnId, [...(byTurn.get(turnId) ?? []), notice]);
	}
	return byTurn;
}
