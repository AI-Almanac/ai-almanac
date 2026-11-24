import { afterEach, describe, expect, it, vi } from 'vitest';

import { tabConversationKey } from '../src/lib/chat/conversation-key';
import { randomId } from '../src/lib/random-id';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

// A plain-HTTP page (not HTTPS, not localhost): browsers leave crypto.randomUUID out,
// so these stub a crypto with getRandomValues only.
afterEach(() => {
	vi.unstubAllGlobals();
	sessionStorage.clear();
});

describe('randomId', () => {
	it('uses crypto.randomUUID where there is one', () => {
		const spy = vi.spyOn(crypto, 'randomUUID');
		expect(randomId()).toMatch(UUID_V4);
		expect(spy).toHaveBeenCalledOnce();
		spy.mockRestore();
	});

	it('makes a version 4 UUID without crypto.randomUUID (a plain-HTTP page)', () => {
		const realCrypto = globalThis.crypto;
		vi.stubGlobal('crypto', {
			getRandomValues: <T extends ArrayBufferView>(array: T) => realCrypto.getRandomValues(array)
		});
		const ids = new Set(Array.from({ length: 200 }, () => randomId()));
		expect(ids.size).toBe(200);
		for (const id of ids) expect(id).toMatch(UUID_V4);
	});
});

describe('tabConversationKey on a plain-HTTP page', () => {
	it('still makes and keeps a key, so the page renders', () => {
		const realCrypto = globalThis.crypto;
		vi.stubGlobal('crypto', {
			getRandomValues: <T extends ArrayBufferView>(array: T) => realCrypto.getRandomValues(array)
		});
		const key = tabConversationKey('assistant');
		expect(key).toMatch(UUID_V4);
		expect(tabConversationKey('assistant')).toBe(key);
		expect(tabConversationKey('assistant', { fresh: true })).not.toBe(key);
	});
});
