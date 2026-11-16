import type { ChatMessage, ChatScope } from '$lib/api';

export const BETA_NOTICE =
	'The assistant is in development and can be wrong. It reads real benchmark and blend data through tools, but check any number that matters against the results pages before relying on it.';

const BETA_NOTICE_SEEN_KEY = 'almanac.assistantBetaNoteDismissed';

/**
 * A workspace gives the conversation a narrow column, so the toolbar's controls
 * fold into one menu beside the composer instead of taking a band of their own.
 */
export function foldsToolbar(scopeKind: ChatScope['kind']): boolean {
	return scopeKind === 'workspace';
}

/**
 * The workspace column's "seen once" is a flag of its own, so reading the notice
 * there does not retire it on the pages where it stays until it is dismissed.
 */
const BETA_NOTICE_SEEN_IN_WORKSPACE_KEY = 'almanac.assistantBetaNoteSeenInWorkspace';

export function betaNoticeKey(scopeKind: ChatScope['kind']): string {
	return foldsToolbar(scopeKind) ? BETA_NOTICE_SEEN_IN_WORKSPACE_KEY : BETA_NOTICE_SEEN_KEY;
}

/** The full notice is owed until it has been seen or dismissed on this device. */
export function betaNoticeDue(stored: string | null): boolean {
	return stored !== '1';
}

/** The conversation as plain text, for the clipboard. */
export function transcriptText(messages: Pick<ChatMessage, 'role' | 'content'>[]): string {
	return messages
		.map((message) => `${message.role === 'user' ? 'You' : 'AI'}: ${message.content}`)
		.join('\n\n');
}
