import { afterEach, describe, expect, it, vi } from 'vitest';

import { copyText } from '../src/lib/clipboard';

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('copyText', () => {
	it('uses navigator.clipboard where there is one', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		vi.stubGlobal('navigator', { clipboard: { writeText } });
		await copyText('hello');
		expect(writeText).toHaveBeenCalledWith('hello');
	});

	it('falls back to the copy command without navigator.clipboard (a plain-HTTP page)', async () => {
		vi.stubGlobal('navigator', {});
		let copied = '';
		const exec = vi.fn((command: string) => {
			copied = command === 'copy' ? (document.activeElement as HTMLTextAreaElement).value : '';
			return true;
		});
		Object.defineProperty(document, 'execCommand', { value: exec, configurable: true });
		await copyText('hello');
		expect(exec).toHaveBeenCalledWith('copy');
		expect(copied).toBe('hello');
		expect(document.querySelector('textarea')).toBeNull();
	});

	it('reports a copy the browser refused', async () => {
		vi.stubGlobal('navigator', {});
		Object.defineProperty(document, 'execCommand', { value: () => false, configurable: true });
		await expect(copyText('hello')).rejects.toThrow();
		expect(document.querySelector('textarea')).toBeNull();
	});
});
