import { describe, expect, it, vi } from 'vitest';

const { goto, page } = vi.hoisted(() => ({ goto: vi.fn(), page: { url: new URL('http://x/') } }));
vi.mock('$app/navigation', () => ({ goto }));
vi.mock('$app/state', () => ({ page }));

import { takeSetupRequest } from '../src/lib/setup-request';

describe('takeSetupRequest', () => {
	it('removes the flag from the URL and keeps the other params', () => {
		vi.useFakeTimers();
		page.url = new URL('http://x/benchmarks?group=g1&manual=1');

		expect(takeSetupRequest('manual')).toBe(true);
		vi.runAllTimers();
		expect(String(goto.mock.calls[0][0])).toBe('http://x/benchmarks?group=g1');
		expect(goto.mock.calls[0][1]).toMatchObject({ replaceState: true });
	});

	it('does nothing when setup was not requested', () => {
		vi.useFakeTimers();
		page.url = new URL('http://x/blends');

		expect(takeSetupRequest('new')).toBe(false);
		vi.runAllTimers();
		expect(goto).not.toHaveBeenCalled();
	});
});
