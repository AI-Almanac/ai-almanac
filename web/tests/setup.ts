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

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
	vi.restoreAllMocks();
});
