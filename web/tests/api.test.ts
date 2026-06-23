import { describe, expect, it, vi } from 'vitest';

import { sendChatMessage, usesBearerAuth, type ChatEvent } from '../src/lib/api';

describe('usesBearerAuth', () => {
	it('uses proxy-provided identity without requiring a browser token', () => {
		expect(usesBearerAuth({ authMode: 'proxy' }, 'globus-client')).toBe(false);
	});

	it('requires a browser token for Globus auth', () => {
		expect(usesBearerAuth({ authMode: 'globus' }, undefined)).toBe(true);
	});

	it('falls back to the build-time Globus client for the Vite dev server', () => {
		expect(usesBearerAuth(undefined, 'globus-client')).toBe(true);
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
		const fetchMock = vi
			.spyOn(globalThis, 'fetch')
			.mockResolvedValue(
				streamingResponse([
					'data: {"type":"text_delta","turn_id":"turn-1","content":"Hel',
					'lo"}\n',
					'data: {"type":"done","turn":{"id":"turn-1","role":"assistant","content":"Hello","created_at":"2026-04-23T00:00:00Z"}}\n'
				])
			);

		const events = await collectEvents(sendChatMessage('session-1', 'hi'));

		expect(fetchMock).toHaveBeenCalledTimes(1);
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
