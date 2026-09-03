import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/svelte';

// jsdom implements no ResizeObserver, and components that keep a canvas fitted to
// its container construct one during an effect. Thrown there it surfaces as an
// unhandled error while the test still passes, so the stub keeps a real failure
// from hiding behind noise.
if (!('ResizeObserver' in globalThis)) {
	globalThis.ResizeObserver = class {
		observe() {}
		unobserve() {}
		disconnect() {}
	} as unknown as typeof ResizeObserver;
}

// Node 22 defines globalThis.localStorage as an experimental getter that
// evaluates to undefined without --localstorage-file — and because the global
// exists, the jsdom environment does not install its own. Node 24 has no such
// global, jsdom's real Storage lands, and the same suite passes. When Node's
// broken global is in play, replace both storages with an in-memory Storage so
// the tests see one behavior on every Node version (and a fresh area per test
// file, rather than Node's process-wide one).
function storageWorks(storage: () => Storage | undefined): boolean {
	try {
		return typeof storage()?.getItem === 'function';
	} catch {
		return false;
	}
}
if (!storageWorks(() => globalThis.localStorage)) {
	const memoryStorage = (): Storage => {
		const area = new Map<string, string>();
		return {
			getItem: (key: string) => area.get(key) ?? null,
			setItem: (key: string, value: string) => void area.set(key, String(value)),
			removeItem: (key: string) => void area.delete(key),
			clear: () => area.clear(),
			key: (index: number) => [...area.keys()][index] ?? null,
			get length() {
				return area.size;
			}
		} as Storage;
	};
	Object.defineProperty(globalThis, 'localStorage', {
		value: memoryStorage(),
		configurable: true
	});
	Object.defineProperty(globalThis, 'sessionStorage', {
		value: memoryStorage(),
		configurable: true
	});
}

// jsdom does not implement scrollIntoView; tours call it on every highlighted element.
Element.prototype.scrollIntoView ??= () => {};

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
	vi.restoreAllMocks();
});
