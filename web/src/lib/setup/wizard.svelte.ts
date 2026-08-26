/**
 * Setup wizard state machine.
 *
 * Steps (in order): system → storage → llm → envs → finish
 * Re-entrant: if envs prepare is already running when the wizard loads,
 * it jumps to the envs step and reattaches to the live SSE stream.
 */
import {
	getSetupState,
	streamPrepareEvents,
	type PrepareEvent,
	type SetupState
} from '$lib/api/setup';

export type WizardStep = 'system' | 'storage' | 'llm' | 'envs' | 'finish';

const STEPS: WizardStep[] = ['system', 'storage', 'llm', 'envs', 'finish'];

export class SetupWizardState {
	step = $state<WizardStep>('system');
	state = $state<SetupState | null>(null);
	loading = $state(true);
	error = $state<string | null>(null);

	// Per-step errors
	storageError = $state<string | null>(null);
	llmError = $state<string | null>(null);

	// Envs / prepare step
	prepareLog = $state<string[]>([]);
	prepareStatus = $state<'idle' | 'running' | 'done' | 'failed'>('idle');
	envStatus = $state<Record<string, string>>({});
	lastSeq = $state(-1);
	streaming = $state(false);

	async load(): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			this.state = await getSetupState();
			this.envStatus = (this.state.envs as Record<string, string>) ?? {};
			const prepare = this.state.prepare as { status: string; last_seq: number } | undefined;
			if (prepare?.status === 'running') {
				this.prepareStatus = 'running';
				this.lastSeq = prepare.last_seq ?? -1;
				this.step = 'envs';
				void this.attachStream(this.lastSeq);
			} else if (prepare?.status === 'done') {
				this.prepareStatus = 'done';
				this.step = 'envs';
			} else if (prepare?.status === 'failed') {
				this.prepareStatus = 'failed';
				this.step = 'envs';
			} else {
				// Jump to earliest incomplete step
				this.step = this._firstIncomplete();
			}
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		} finally {
			this.loading = false;
		}
	}

	private _firstIncomplete(): WizardStep {
		// system is always complete once state loads
		// storage: output_dir is optional, so we skip to llm
		const s = this.state;
		if (!s) return 'system';
		const llm = s.llm as { configured?: boolean } | undefined;
		if (!llm?.configured) return 'llm';
		if (this.prepareStatus === 'idle') return 'envs';
		return 'finish';
	}

	goTo(step: WizardStep): void {
		this.step = step;
	}

	goNext(): void {
		const idx = STEPS.indexOf(this.step);
		if (idx < STEPS.length - 1) this.step = STEPS[idx + 1];
	}

	goPrev(): void {
		const idx = STEPS.indexOf(this.step);
		if (idx > 0) this.step = STEPS[idx - 1];
	}

	async attachStream(after: number): Promise<void> {
		if (this.streaming) return;
		this.streaming = true;
		try {
			for await (const evt of streamPrepareEvents(after)) {
				this._handleEvent(evt);
				if (evt.type === 'done') break;
			}
		} catch (e) {
			this.prepareLog.push(`[error] ${e instanceof Error ? e.message : String(e)}`);
			if (this.prepareStatus === 'running') this.prepareStatus = 'failed';
		} finally {
			this.streaming = false;
		}
	}

	private _handleEvent(evt: PrepareEvent): void {
		if (evt.type === 'keepalive') return;
		if (evt.type === 'state') {
			this.prepareStatus = evt.status as 'idle' | 'running' | 'done' | 'failed';
			this.envStatus = evt.envs;
			if ('seq' in evt && evt.seq > this.lastSeq) this.lastSeq = evt.seq;
			return;
		}
		if ('seq' in evt && evt.seq <= this.lastSeq) return; // dedup on reattach
		if ('seq' in evt) this.lastSeq = evt.seq;
		if (evt.type === 'env') {
			if (evt.kind === 'line' && evt.line) {
				this.prepareLog.push(evt.line);
				if (this.prepareLog.length > 500) this.prepareLog.splice(0, this.prepareLog.length - 500);
			} else if (evt.kind === 'phase_started' || evt.kind === 'phase_finished') {
				this.prepareLog.push(`▸ [${evt.phase}] ${evt.detail ?? evt.kind}`);
			} else if (evt.kind === 'phase_failed') {
				this.prepareLog.push(`✗ [${evt.phase}] ${evt.detail ?? 'failed'}`);
			} else if (evt.kind === 'phase_skipped') {
				this.prepareLog.push(`— [${evt.phase}] ${evt.detail ?? 'skipped'}`);
			}
		} else if (evt.type === 'done') {
			this.prepareStatus = evt.ok ? 'done' : 'failed';
			this.envStatus = evt.envs;
			if (!evt.ok && evt.error) this.prepareLog.push(`[error] ${evt.error}`);
		}
	}
}
