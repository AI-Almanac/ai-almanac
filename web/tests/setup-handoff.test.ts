import { describe, expect, it } from 'vitest';
import type { ChatMessage, ChatToolCall } from '../src/lib/api';
import { setupHandoffs } from '../src/lib/chat/handoff';

function turnWith(...calls: Partial<ChatToolCall>[]): ChatMessage {
	return {
		id: 't1',
		role: 'assistant',
		content: '',
		created_at: '2026-10-13T00:00:00Z',
		tool_calls: calls.map((call, i) => ({
			id: `c${i}`,
			name: 'open_setup',
			status: 'completed',
			input: {},
			artifacts: [],
			...call
		}))
	};
}

describe('setupHandoffs', () => {
	it('links a benchmark request to benchmark setup with the request as its prompt', () => {
		const [handoff] = setupHandoffs(
			turnWith({
				result: { setup_handoff: { workflow: 'benchmark', request: 'Benchmark Kiremt & more' } }
			})
		);

		expect(handoff.workflow).toBe('benchmark');
		const url = new URL(handoff.href, 'http://x');
		expect(url.pathname).toBe('/benchmarks');
		expect(url.searchParams.get('q')).toBe('Benchmark Kiremt & more');
	});

	it('links a blend request to blend setup', () => {
		const [handoff] = setupHandoffs(
			turnWith({ result: { setup_handoff: { workflow: 'blend', request: 'Blend for Ethiopia' } } })
		);

		expect(new URL(handoff.href, 'http://x').pathname).toBe('/blends');
	});

	it('offers no link for a call that has not finished, failed, or is another tool', () => {
		const result = { setup_handoff: { workflow: 'benchmark', request: 'x' } };

		expect(
			setupHandoffs(
				turnWith(
					{ status: 'running', result },
					{ status: 'failed', result },
					{ name: 'list_regions', result }
				)
			)
		).toEqual([]);
	});

	it('offers no link for a result it does not recognise', () => {
		expect(
			setupHandoffs(
				turnWith(
					{ result: { setup_handoff: { workflow: 'forecast', request: 'x' } } },
					{ result: { setup_handoff: { workflow: 'blend', request: '  ' } } },
					{ result: null }
				)
			)
		).toEqual([]);
	});
});
