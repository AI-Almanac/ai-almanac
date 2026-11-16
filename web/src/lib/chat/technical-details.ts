/**
 * What sits behind a step of the assistant's work for whoever wants to check it:
 * the tool it called and what it passed. The step itself is shown in plain words.
 */
import type { ChatToolCall } from '$lib/api';
import { codeForToolCall } from './tools';

export type TechnicalDetails = {
	tool: string;
	/** The call's arguments as indented JSON, without the code or any internal id; null when none are left. */
	arguments: string | null;
	code: string | null;
};

const ID_KEY = /(^|_)ids?$/i;
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

function isInternalId(key: string | null, value: unknown): boolean {
	if (key !== null && ID_KEY.test(key)) return true;
	return typeof value === 'string' && UUID.test(value);
}

/** The value with every internal id left out, however deep it sits. */
function withoutIds(value: unknown): unknown {
	if (Array.isArray(value)) {
		return value.filter((item) => !isInternalId(null, item)).map(withoutIds);
	}
	if (value === null || typeof value !== 'object') return value;
	return Object.fromEntries(
		Object.entries(value)
			.filter(([key, inner]) => !isInternalId(key, inner))
			.map(([key, inner]) => [key, withoutIds(inner)])
	);
}

export function technicalDetails(toolCall: ChatToolCall): TechnicalDetails {
	const code = codeForToolCall(toolCall);
	const passed = Object.entries(toolCall.input ?? {}).filter(
		([key]) => !(code !== null && key === 'code')
	);
	const shown = withoutIds(Object.fromEntries(passed)) as Record<string, unknown>;
	return {
		tool: toolCall.name,
		arguments: Object.keys(shown).length > 0 ? JSON.stringify(shown, null, 2) : null,
		code
	};
}
