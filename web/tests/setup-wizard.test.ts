/**
 * Tests for the setup wizard:
 * - sseEvents parser
 * - token capture and header injection
 * - SetupWizardState step transitions and SSE reattach/dedup
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sseEvents } from '../src/lib/api/sse';
import { getSetupToken, storeSetupToken, type PrepareEvent } from '../src/lib/api/setup';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sseStream(chunks: string[]): Response {
	const encoder = new TextEncoder();
	return new Response(
		new ReadableStream({
			start(ctrl) {
				for (const chunk of chunks) ctrl.enqueue(encoder.encode(chunk));
				ctrl.close();
			}
		}),
		{ status: 200 }
	);
}

async function collect<T>(gen: AsyncGenerator<T>): Promise<T[]> {
	const items: T[] = [];
	for await (const item of gen) items.push(item);
	return items;
}

// ---------------------------------------------------------------------------
// sseEvents
// ---------------------------------------------------------------------------

describe('sseEvents', () => {
	it('parses a single-chunk SSE stream', async () => {
		const res = sseStream(['data: {"type":"state","seq":-1}\n\n']);
		const events = await collect(sseEvents<{ type: string; seq: number }>(res));
		expect(events).toHaveLength(1);
		expect(events[0]).toEqual({ type: 'state', seq: -1 });
	});

	it('parses events split across multiple chunks', async () => {
		const res = sseStream([
			'data: {"type":"env","seq":0,"kind":"phase_start',
			'ed","phase":"benchmark"}\n\n',
			'data: {"type":"done","seq":1,"ok":true,"error":null,"envs":{}}\n\n'
		]);
		const events = await collect(sseEvents<PrepareEvent>(res));
		expect(events).toHaveLength(2);
		expect(events[0]).toMatchObject({ type: 'env', seq: 0 });
		expect(events[1]).toMatchObject({ type: 'done', ok: true });
	});

	it('skips keepalive comment lines', async () => {
		const res = sseStream([
			': keepalive\n\n',
			'data: {"type":"state","seq":-1,"status":"idle","envs":{}}\n\n'
		]);
		const events = await collect(sseEvents<PrepareEvent>(res));
		expect(events).toHaveLength(1);
		expect(events[0]).toMatchObject({ type: 'state' });
	});

	it('handles a stream that ends without a trailing newline', async () => {
		const res = sseStream(['data: {"type":"done","seq":0,"ok":true,"error":null,"envs":{}}']);
		const events = await collect(sseEvents<PrepareEvent>(res));
		expect(events).toHaveLength(1);
		expect(events[0]).toMatchObject({ type: 'done' });
	});
});

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

describe('setup token', () => {
	beforeEach(() => sessionStorage.clear());
	afterEach(() => sessionStorage.clear());

	it('round-trips through sessionStorage', () => {
		storeSetupToken('tok-abc123');
		expect(getSetupToken()).toBe('tok-abc123');
	});

	it('returns null when no token stored', () => {
		expect(getSetupToken()).toBeNull();
	});
});

// ---------------------------------------------------------------------------
// SetupWizardState — step transitions + SSE event handling
// ---------------------------------------------------------------------------

describe('SetupWizardState', () => {
	beforeEach(() => sessionStorage.clear());

	it('starts at the system step', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		expect(w.step).toBe('system');
	});

	it('advances through steps with goNext and retreats with goPrev', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		w.goNext();
		expect(w.step).toBe('storage');
		w.goNext();
		expect(w.step).toBe('llm');
		w.goPrev();
		expect(w.step).toBe('storage');
	});

	it('goTo jumps directly to a named step', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		w.goTo('finish');
		expect(w.step).toBe('finish');
	});

	it('does not advance past the last step', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		w.goTo('finish');
		w.goNext();
		expect(w.step).toBe('finish');
	});

	it('does not retreat before the first step', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		w.goPrev();
		expect(w.step).toBe('system');
	});

	it('deduplicates events by seq on reattach', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		type HandleEvent = (e: PrepareEvent) => void;

		// Set lastSeq = 0 to simulate reattach after seeing seq 0
		w.lastSeq = 0;
		w.prepareStatus = 'running';

		const handle = (w as unknown as { _handleEvent: HandleEvent })._handleEvent.bind(w);

		// state snapshot (always emitted on connect, seq -1)
		handle({ type: 'state', seq: -1, status: 'running', envs: {} });
		// seq 0 already seen — should be deduplicated
		handle({ type: 'env', seq: 0, kind: 'line', phase: 'benchmark', line: 'first line' });
		// seq 1 is new
		handle({ type: 'env', seq: 1, kind: 'line', phase: 'benchmark', line: 'second line' });
		handle({ type: 'done', seq: 2, ok: true, error: null, envs: { benchmark: 'ready' } });

		expect(w.prepareLog).not.toContain('first line');
		expect(w.prepareLog).toContain('second line');
		expect(w.prepareStatus).toBe('done');
		expect(w.envStatus).toEqual({ benchmark: 'ready' });
	});

	it('accumulates log lines from env events', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		type HandleEvent = (e: PrepareEvent) => void;
		const handle = (w as unknown as { _handleEvent: HandleEvent })._handleEvent.bind(w);

		handle({ type: 'state', seq: -1, status: 'running', envs: {} });
		handle({ type: 'env', seq: 0, kind: 'phase_started', phase: 'benchmark', detail: 'Solving' });
		handle({ type: 'env', seq: 1, kind: 'line', phase: 'benchmark', line: '  + numpy 2.1.0' });
		handle({ type: 'env', seq: 2, kind: 'phase_finished', phase: 'benchmark', detail: 'done' });

		expect(w.prepareLog.some((l) => l.includes('Solving'))).toBe(true);
		expect(w.prepareLog).toContain('  + numpy 2.1.0');
		expect(w.prepareLog.some((l) => l.includes('done'))).toBe(true);
	});

	it('sets prepareStatus=done on done event with ok=true', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		type HandleEvent = (e: PrepareEvent) => void;
		const handle = (w as unknown as { _handleEvent: HandleEvent })._handleEvent.bind(w);

		w.prepareStatus = 'running';
		handle({ type: 'done', seq: 0, ok: true, error: null, envs: { benchmark: 'ready' } });
		expect(w.prepareStatus).toBe('done');
	});

	it('sets prepareStatus=failed on done event with ok=false', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');
		const w = new SetupWizardState();
		type HandleEvent = (e: PrepareEvent) => void;
		const handle = (w as unknown as { _handleEvent: HandleEvent })._handleEvent.bind(w);

		w.prepareStatus = 'running';
		handle({ type: 'done', seq: 0, ok: false, error: 'pixi failed', envs: {} });
		expect(w.prepareStatus).toBe('failed');
		expect(w.prepareLog.some((l) => l.includes('pixi failed'))).toBe(true);
	});

	it('load() with running prepare status jumps to envs step', async () => {
		const { SetupWizardState } = await import('../src/lib/setup/wizard.svelte');

		storeSetupToken('test-tok');

		const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(
				JSON.stringify({
					platform: { platform: 'linux-64', machine: 'x86_64' },
					gpu: null,
					data_dir: '/tmp/data',
					config_yaml_path: '/tmp/data/config.yaml',
					dataset_mount_roots: [],
					llm: { configured: true, base_url: 'http://localhost/v1', model: 'llama3' },
					envs: { benchmark: 'missing' },
					prepare: { status: 'running', last_seq: 3 }
				}),
				{ status: 200, headers: { 'Content-Type': 'application/json' } }
			)
		);

		// Mock the SSE stream call so attachStream resolves immediately
		fetchSpy.mockResolvedValueOnce(
			sseStream(['data: {"type":"state","seq":-1,"status":"running","envs":{}}\n\n'])
		);

		const w = new SetupWizardState();
		await w.load();

		expect(w.step).toBe('envs');
		expect(w.prepareStatus).toBe('running');
		fetchSpy.mockRestore();
	});
});
