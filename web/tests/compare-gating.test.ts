import { describe, expect, it } from 'vitest';

import { compareBlocker } from '$lib/chat/compare.svelte';

describe('when A/B is offered', () => {
	it('needs two published styles', () => {
		expect(compareBlocker(false, 'which model is best?')).toMatch(/administrator/i);
	});

	it('runs on typed text', () => {
		expect(compareBlocker(true, 'which model is best?')).toBeNull();
	});

	it('runs on the last question when the box is empty', () => {
		// The caller passes the last question as the text, so comparing the answer
		// already on screen works without retyping it.
		expect(compareBlocker(true, 'the previous question')).toBeNull();
	});

	it('explains itself with nothing to compare at all', () => {
		expect(compareBlocker(true, '')).toMatch(/type a question/i);
		expect(compareBlocker(true, '   ')).toMatch(/type a question/i);
	});
});
