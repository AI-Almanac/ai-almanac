import type { ChatToolCall } from '../../src/lib/api';

/** A finished call of the tool `name`, with no input and nothing made. */
export function call(name: string, overrides: Partial<ChatToolCall> = {}): ChatToolCall {
	return { id: name, name, status: 'completed', input: {}, artifacts: [], ...overrides };
}
