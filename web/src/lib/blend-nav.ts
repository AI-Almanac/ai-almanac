import { goto } from '$app/navigation';
import type { ChatScope } from '$lib/api';

/**
 * Navigate to the Blends page for a just-launched blend, carrying the chat
 * session so the conversation continues seamlessly across the flow. The
 * originating scope is passed through so follow-up messages keep matching the
 * session's stored scope.
 */
export function goToBlend(
	blendId: string | undefined,
	sessionId: string | null,
	scope: { kind: ChatScope['kind']; key: string }
): Promise<void> {
	const params = new URLSearchParams();
	if (blendId) params.set('blend', blendId);
	if (sessionId) {
		params.set('chat', sessionId);
		params.set('scopeKind', scope.kind);
		params.set('scopeKey', scope.key);
	}
	return goto(`/blends?${params.toString()}`);
}
