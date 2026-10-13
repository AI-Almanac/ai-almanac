import { describe, expect, it } from 'vitest';

import { compareBlocker } from '$lib/chat/compare.svelte';

describe('when A/B is offered', () => {
	it.each([
		{
			case: 'needs two published styles',
			ready: false,
			text: 'which model is best?',
			blocker: /administrator/i
		},
		{ case: 'runs on typed text', ready: true, text: 'which model is best?', blocker: null },
		{
			case: 'explains itself with nothing to compare',
			ready: true,
			text: '',
			blocker: /type a question/i
		},
		{
			case: 'treats whitespace as nothing to compare',
			ready: true,
			text: '   ',
			blocker: /type a question/i
		}
	])('$case', ({ ready, text, blocker }) => {
		const result = compareBlocker(ready, text);
		if (blocker === null) expect(result).toBeNull();
		else expect(result).toMatch(blocker);
	});
});
