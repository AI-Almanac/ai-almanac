/**
 * The scope key for a page's conversation, kept for the browser tab.
 *
 * Chat sessions are listed per scope, so a key made fresh on every visit loses
 * the conversation as soon as the user navigates away, including one the
 * assistant is still answering. Keeping the key in sessionStorage brings the
 * same conversation back on return; the chat header's "new chat" still starts
 * another, and a new tab starts fresh.
 *
 * Pass `fresh` when the visit brings its own opening question (a `?q=` link),
 * which should begin a conversation of its own.
 */
export function tabConversationKey(page: string, { fresh = false } = {}): string {
	const storageKey = `ai-almanac:conversation:${page}`;
	try {
		const saved = fresh ? null : sessionStorage.getItem(storageKey);
		if (saved) return saved;
		const key = crypto.randomUUID();
		sessionStorage.setItem(storageKey, key);
		return key;
	} catch {
		// Storage can be unavailable (private modes, server rendering).
		return crypto.randomUUID();
	}
}
