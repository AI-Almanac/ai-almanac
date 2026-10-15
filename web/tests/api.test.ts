import { describe, expect, it, vi } from 'vitest';

import { sendChatMessage, usesBearerAuth, type ChatEvent } from '../src/lib/api';

describe('usesBearerAuth', () => {
	it.each([
		{
			case: 'uses proxy-provided identity without requiring a browser token',
			config: { authMode: 'proxy' as const },
			clientId: 'globus-client',
			bearer: false
		},
		{
			case: 'requires a browser token for Globus auth',
			config: { authMode: 'globus' as const },
			clientId: undefined,
			bearer: true
		},
		{
			case: 'falls back to the build-time Globus client for the Vite dev server',
			config: undefined,
			clientId: 'globus-client',
			bearer: true
		}
	])('$case', ({ config, clientId, bearer }) => {
		expect(usesBearerAuth(config, clientId)).toBe(bearer);
	});
});

function streamingResponse(chunks: string[]): Response {
	const encoder = new TextEncoder();
	return new Response(
		new ReadableStream({
			start(controller) {
				for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
				controller.close();
			}
		}),
		{ status: 200 }
	);
}

async function collectEvents(stream: AsyncGenerator<ChatEvent>): Promise<ChatEvent[]> {
	const events: ChatEvent[] = [];
	for await (const event of stream) events.push(event);
	return events;
}

describe('sendChatMessage', () => {
	it('parses chunked SSE and stops on done', async () => {
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(
			streamingResponse([
				'data: {"type":"text_delta","turn_id":"turn-1","content":"Hel',
				'lo"}\n',
				'data: {"type":"done","turn":{"id":"turn-1","role":"assistant","content":"Hello","created_at":"2026-04-23T00:00:00Z"}}\n'
			])
		);

		const events = await collectEvents(sendChatMessage('session-1', 'hi'));

		expect(events).toEqual([
			{ type: 'text_delta', turn_id: 'turn-1', content: 'Hello' },
			{
				type: 'done',
				turn: {
					id: 'turn-1',
					role: 'assistant',
					content: 'Hello',
					created_at: '2026-04-23T00:00:00Z'
				}
			}
		]);
	});

	it('throws on premature EOF without a terminal event', async () => {
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(
			streamingResponse(['data: {"type":"text_delta","turn_id":"turn-1","content":"Partial"}\n'])
		);

		await expect(collectEvents(sendChatMessage('session-1', 'hi'))).rejects.toThrow(
			'Chat stream ended before a terminal event was received.'
		);
	});
});
