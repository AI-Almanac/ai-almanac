import { describe, expect, it } from 'vitest';

import { keepView } from '../src/lib/workspace/canvas-view';

const view = (key: string, usedAt: number) => ({ key, usedAt });

describe('which product views the stage keeps', () => {
	it('adds a new view after the ones it already keeps', () => {
		expect(keepView([view('a', 1)], view('b', 2), 3).map((v) => v.key)).toEqual(['a', 'b']);
	});

	it('keeps a view it holds where it is, marked as just used', () => {
		const kept = keepView([view('a', 1), view('b', 2)], view('a', 3), 3);

		expect(kept).toEqual([view('a', 3), view('b', 2)]);
	});

	it('drops the view used longest ago, never the one being shown', () => {
		const kept = keepView([view('a', 2), view('b', 1), view('c', 3)], view('d', 4), 3);

		expect(kept.map((v) => v.key)).toEqual(['a', 'c', 'd']);
	});
});
