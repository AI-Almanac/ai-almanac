import {
	createChatSession,
	getChatSessions,
	getChatSession,
	updateChatSession,
	deleteChatSession,
	getChatJobActivity,
	sendChatMessage,
	submitChatBenchmark,
	denyChatBenchmarkApproval,
	submitChatBlend,
	denyChatBlendApproval,
	getAssistantStartup,
	stopChatReply,
	type AssistantStartup,
	type BenchmarkRunSpec,
	type BenchmarkValidation,
	type Blend,
	type BlendRunSpec,
	type BlendValidation,
	type ChatEvent,
	type ChatJobActivity,
	type ChatMessage,
	type ChatScope,
	type ChatSession,
	type Job
} from '$lib/api';
import { emptyAssistantTurn, foldTurnEvent, startsTurn } from '$lib/chat/turn';

export interface ChatSessionCallbacks {
	onSessionReady?: (sessionId: string) => void;
	onJobsCreated?: (jobs: Job[]) => void;
	onBenchmarkConfig?: (config: BenchmarkRunSpec, validation?: BenchmarkValidation | null) => void;
	onBenchmarkSubmitted?: (runId: string, jobs: Job[], sessionId: string | null) => void;
	onBlendConfig?: (config: BlendRunSpec, validation?: BlendValidation | null) => void;
	onBlendSubmitted?: (runId: string, jobs: Blend[], sessionId: string | null) => void;
}

function scopeToken(scope: ChatScope): string {
	const jobIds = [...scope.job_ids].sort().join(',');
	return `${scope.kind}:${scope.key}:${jobIds}`;
}

function byUpdatedAt(a: ChatSession, b: ChatSession): number {
	return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
}

function mergeArtifacts(base: ChatMessage | null, incoming: ChatMessage): ChatMessage {
	const byId = new Map<string, NonNullable<ChatMessage['artifacts']>[number]>();
	for (const artifact of base?.artifacts ?? []) byId.set(artifact.id, artifact);
	for (const artifact of incoming.artifacts ?? []) byId.set(artifact.id, artifact);
	return { ...incoming, artifacts: [...byId.values()] };
}

function jobFromToolResult(result: unknown): Job | null {
	if (!result || typeof result !== 'object' || !('job' in result)) return null;
	const job = (result as { job?: unknown }).job;
	if (!job || typeof job !== 'object' || !('id' in job)) return null;
	return job as Job;
}

// How often a reopened chat checks on a reply still being written, and how long
// before one is given up on: beyond the server's 30-minute wait for a starting
// model, plus time to answer.
const EARLIER_REPLY_POLL_MS = 3000;
const EARLIER_REPLY_TIMEOUT_MS = 45 * 60 * 1000;

function chatErrorMessage(event: Extract<ChatEvent, { type: 'error' }>): string {
	if (event.error_type === 'provider_error') return 'The chat provider failed. Please try again.';
	if (event.error_type === 'tool_error') return 'A benchmark data lookup failed. Please try again.';
	if (event.error_type === 'context_overflow') {
		return "This conversation has grown too long for the assistant's memory. Start a new chat to continue.";
	}
	if (event.error_type === 'repetition') {
		return 'The assistant got stuck repeating itself, so its reply was stopped. Try asking again, or rephrase.';
	}
	// The locally run model is paused for a forecast or still loading; the
	// server's message says which.
	if (event.error_type === 'assistant_unavailable') return event.message;
	if (event.error_type === 'scope_mismatch') {
		return 'This chat belongs to a different benchmark view. Start a new chat for this benchmark.';
	}
	return event.message || 'Chat request failed.';
}

/**
 * Owns chat session state: the session list for the active scope, the loaded
 * transcript, the streaming assistant turn, and benchmark approval flow.
 * Components render this state and call the action methods.
 */
export class ChatSessionState {
	sessions = $state<ChatSession[]>([]);
	sessionId = $state<string | null>(null);
	messages = $state<ChatMessage[]>([]);
	streamingTurn = $state<ChatMessage | null>(null);
	// Set while a locally run model reloads its weights before answering.
	assistantWaking = $state(false);
	// The latest progress while a locally run model starts; the message waits on it.
	assistantStartup = $state<AssistantStartup | null>(null);
	// A reply sent before this page was (re)opened that the server is still
	// writing; followed until it lands, and sending waits for it.
	earlierReplyId = $state<string | null>(null);
	// From a stop request until the reply has ended.
	stopping = $state(false);
	jobActivity = $state<ChatJobActivity>({ events: [], active: 0 });
	sending = $state(false);
	error = $state<string | null>(null);
	loadingSession = $state(false);
	pendingSubmission = $state<
		| { kind: 'benchmark'; runId: string; jobs: Job[] }
		| { kind: 'blend'; runId: string; jobs: Blend[] }
		| null
	>(null);
	pendingApproval = $state<
		| {
				kind: 'benchmark';
				toolCallId: string;
				config: BenchmarkRunSpec;
				validation: BenchmarkValidation | null;
		  }
		| {
				kind: 'blend';
				toolCallId: string;
				config: BlendRunSpec;
				validation: BlendValidation | null;
		  }
		| null
	>(null);

	readonly visibleTurns = $derived(
		this.streamingTurn ? [...this.messages, this.streamingTurn] : this.messages
	);
	readonly currentSession = $derived(this.sessions.find((s) => s.id === this.sessionId));

	private sendLocked = false;
	private loadedScopeToken: string | null = null;
	private disposed = false;
	private abort: AbortController | null = null;

	constructor(
		private getScope: () => ChatScope,
		private callbacks: ChatSessionCallbacks = {}
	) {}

	get canSend(): boolean {
		return !this.sending && !this.sendLocked && !this.loadingSession && !this.earlierReplyId;
	}

	/** Whether there is a reply in progress that `stop()` can end. */
	get canStop(): boolean {
		return (this.sending || Boolean(this.earlierReplyId)) && !this.stopping;
	}

	/**
	 * Stop the reply in progress, keeping what it had said. The server stops it
	 * (closing its request to the model) and saves it as stopped; the reply's own
	 * stream then ends with that turn. Only if the server has no such reply here
	 * (another instance is serving it) does this drop the connection instead.
	 */
	async stop() {
		const sessionId = this.sessionId;
		if (!this.canStop || !sessionId) return;
		this.stopping = true;
		let stopped = false;
		try {
			stopped = (await stopChatReply(sessionId)).stopped;
		} catch {
			// Fall through to dropping the connection.
		}
		if (!stopped) this.abort?.abort();
		// A reply followed from an earlier visit has no stream here: the follow
		// loop shows the stopped turn on its next check, and clears this then.
		if (!this.sending && !stopped) this.stopping = false;
	}

	/** Stop following an earlier reply once the panel showing it is gone. */
	dispose() {
		this.disposed = true;
	}

	/** Show a loaded transcript, following its last reply if the server is still writing it. */
	private showTranscript(sessionId: string, transcript: ChatMessage[]) {
		const last = transcript.at(-1);
		if (this.sending || last?.role !== 'assistant' || last.status !== 'streaming') {
			this.messages = transcript;
			this.earlierReplyId = null;
			return;
		}
		// The placeholder has nothing to show yet; the working indicator stands in for it.
		this.messages = transcript.slice(0, -1);
		if (Date.now() - Date.parse(last.created_at) > EARLIER_REPLY_TIMEOUT_MS) {
			// Too old to still be running (the server restarted mid-reply, say).
			this.earlierReplyId = null;
			this.error = 'An earlier reply was interrupted. Send your message again.';
			return;
		}
		void this.followEarlierReply(sessionId, last);
	}

	private async followEarlierReply(sessionId: string, turn: ChatMessage) {
		this.earlierReplyId = turn.id;
		const following = () =>
			!this.disposed && this.sessionId === sessionId && this.earlierReplyId === turn.id;
		const deadline = Date.parse(turn.created_at) + EARLIER_REPLY_TIMEOUT_MS;
		while (following()) {
			try {
				const startup = await getAssistantStartup();
				if (following()) this.assistantStartup = startup && { ...startup, previous_failure: null };
			} catch {
				// Progress is a nicety; the reply is still followed without it.
			}
			await new Promise((resolve) => setTimeout(resolve, EARLIER_REPLY_POLL_MS));
			if (!following()) return;
			try {
				const detail = await getChatSession(sessionId);
				const latest = detail.transcript.find((existing) => existing.id === turn.id);
				if (!following()) return;
				if (latest?.status !== 'streaming') {
					this.messages = detail.transcript;
					this.earlierReplyId = null;
					this.assistantStartup = null;
					this.stopping = false;
					return;
				}
			} catch {
				// A failed refresh is retried on the next tick.
			}
			if (Date.now() > deadline) {
				this.earlierReplyId = null;
				this.assistantStartup = null;
				this.error = 'An earlier reply was interrupted. Send your message again.';
				return;
			}
		}
	}

	/** Reload the session list when the scope identity changes. */
	syncScope(preferredSessionId: string | null = null) {
		const scope = this.getScope();
		const token = scopeToken(scope);
		if (!scope.key || token === this.loadedScopeToken) return;
		this.loadedScopeToken = token;
		this.streamingTurn = null;
		void this.refreshSessionsForScope(preferredSessionId).catch(() => {
			this.error = 'Failed to load chat sessions.';
		});
	}

	private async refreshSessionsForScope(preferredSessionId: string | null) {
		const scope = this.getScope();
		const all = await getChatSessions(scope);
		this.sessions = all;

		if (preferredSessionId && !all.some((session) => session.id === preferredSessionId)) {
			try {
				await this.loadSession(preferredSessionId);
				return;
			} catch {
				// Fall back to scope sessions below; the preferred setup chat may no longer exist.
			}
		}

		const nextSessionId =
			preferredSessionId && all.some((session) => session.id === preferredSessionId)
				? preferredSessionId
				: this.sessionId && all.some((session) => session.id === this.sessionId)
					? this.sessionId
					: (all[0]?.id ?? null);

		if (nextSessionId) {
			await this.loadSession(nextSessionId);
			return;
		}
		await this.createNewSession();
	}

	async loadSession(id: string) {
		this.loadingSession = true;
		this.error = null;
		try {
			const detail = await getChatSession(id);
			this.sessionId = id;
			this.callbacks.onSessionReady?.(id);
			this.showTranscript(id, detail.transcript);
			void this.refreshJobActivity();
			if (detail.benchmark_config) {
				this.callbacks.onBenchmarkConfig?.(
					detail.benchmark_config,
					detail.benchmark_validation ?? null
				);
			}
			if (detail.blend_config) {
				this.callbacks.onBlendConfig?.(detail.blend_config, detail.blend_validation ?? null);
			}
			this.sessions = this.sessions.map((session) =>
				session.id === id ? { ...session, scope: detail.scope } : session
			);
		} catch {
			this.error = 'Failed to load session.';
		} finally {
			this.loadingSession = false;
		}
	}

	async createNewSession(): Promise<string | null> {
		this.error = null;
		try {
			const scope = this.getScope();
			// A new chat keeps the assistant style the user picked for this one.
			const session = await createChatSession(
				scope,
				scope.title ?? undefined,
				this.currentSession?.ruleset_id ?? undefined
			);
			if (session.benchmark_config) {
				this.callbacks.onBenchmarkConfig?.(
					session.benchmark_config,
					session.benchmark_validation ?? null
				);
			}
			if (session.blend_config) {
				this.callbacks.onBlendConfig?.(session.blend_config, session.blend_validation ?? null);
			}
			this.sessions = [session, ...this.sessions].sort(byUpdatedAt);
			this.sessionId = session.id;
			this.callbacks.onSessionReady?.(session.id);
			this.messages = [];
			this.jobActivity = { events: [], active: 0 };
			this.loadedScopeToken = scopeToken(scope);
			return session.id;
		} catch {
			this.error = 'Failed to create session.';
		}
		return null;
	}

	async renameSession(id: string, title: string): Promise<boolean> {
		this.error = null;
		try {
			const updated = await updateChatSession(id, { title: title.trim() || null });
			this.sessions = this.sessions
				.map((s) => (s.id === updated.id ? updated : s))
				.sort(byUpdatedAt);
			return true;
		} catch {
			this.error = 'Failed to rename session.';
			return false;
		}
	}

	async setSessionRuleset(rulesetId: string | null): Promise<boolean> {
		if (!this.sessionId) return false;
		this.error = null;
		try {
			const updated = await updateChatSession(this.sessionId, { ruleset_id: rulesetId });
			this.sessions = this.sessions.map((s) => (s.id === updated.id ? updated : s));
			return true;
		} catch {
			this.error = 'Failed to switch the assistant style.';
			return false;
		}
	}

	async deleteSession(id: string) {
		try {
			await deleteChatSession(id);
			const nextSessions = this.sessions.filter((s) => s.id !== id);
			this.sessions = nextSessions;
			if (this.sessionId === id) {
				if (nextSessions.length > 0) {
					await this.loadSession(nextSessions[0].id);
				} else {
					await this.createNewSession();
				}
			}
		} catch {
			this.error = 'Failed to delete session.';
		}
	}

	async submit(text: string) {
		if (!text || !this.canSend) return;
		this.sendLocked = true;
		const activeSessionId = this.sessionId ?? (await this.createNewSession());
		if (!activeSessionId) {
			this.sendLocked = false;
			return;
		}
		this.sending = true;
		this.assistantWaking = false;
		this.assistantStartup = null;
		this.error = null;
		this.pendingSubmission = null;
		this.pendingApproval = null;

		this.messages = [
			...this.messages,
			{
				id: crypto.randomUUID(),
				role: 'user',
				content: text,
				created_at: new Date().toISOString(),
				tool_calls: []
			}
		];

		this.abort = new AbortController();
		try {
			const scope = this.getScope();
			for await (const event of sendChatMessage(activeSessionId, text, scope, this.abort.signal)) {
				this.applyStreamEvent(event, activeSessionId, scope);
			}
		} catch (e) {
			const message = (e as Error).message ?? 'An error occurred.';
			this.streamingTurn = null;
			await this.loadSession(activeSessionId);
			// A stop the user asked for ends the reply; it isn't an error.
			if (!this.stopping) this.error = message;
		} finally {
			this.abort = null;
			this.stopping = false;
			this.sending = false;
			this.sendLocked = false;
			// A turn can start or rerun a job, or move the chat onto a new run.
			void this.refreshJobActivity();
		}
	}

	/** Re-read which runs in scope have finished; failures keep the last known state. */
	refreshJobActivity = async () => {
		const sessionId = this.sessionId;
		if (!sessionId) return;
		try {
			const activity = await getChatJobActivity(sessionId);
			if (this.sessionId === sessionId) this.jobActivity = activity;
		} catch {
			// Job notices are a convenience; the conversation works without them.
		}
	};

	private applyStreamEvent(event: ChatEvent, activeSessionId: string, scope: ChatScope) {
		if (event.type === 'assistant_status') {
			this.assistantWaking = event.status === 'waking';
			this.assistantStartup = event.status === 'loading' ? event : null;
			return;
		}
		this.assistantWaking = false;
		this.assistantStartup = null;
		if (startsTurn(event) && this.streamingTurn?.id !== event.turn_id) {
			this.streamingTurn = emptyAssistantTurn(event.turn_id);
		}
		if ('turn_id' in event && this.streamingTurn?.id === event.turn_id) {
			this.streamingTurn = foldTurnEvent(this.streamingTurn, event);
		}
		if (event.type === 'tool_result') {
			const createdJob = jobFromToolResult(event.result);
			if (createdJob) this.callbacks.onJobsCreated?.([createdJob]);
		}

		if (event.type === 'error') {
			throw new Error(chatErrorMessage(event));
		} else if (event.type === 'benchmark_approval_request') {
			this.callbacks.onBenchmarkConfig?.(event.config, event.validation ?? null);
			this.pendingApproval = {
				kind: 'benchmark',
				toolCallId: event.tool_call_id,
				config: event.config,
				validation: event.validation ?? null
			};
		} else if (event.type === 'benchmark_config') {
			this.callbacks.onBenchmarkConfig?.(event.config, event.validation ?? null);
			if (event.run_id && event.jobs?.length) {
				this.pendingSubmission = { kind: 'benchmark', runId: event.run_id, jobs: event.jobs };
			}
		} else if (event.type === 'blend_approval_request') {
			this.callbacks.onBlendConfig?.(event.config, event.validation ?? null);
			this.pendingApproval = {
				kind: 'blend',
				toolCallId: event.tool_call_id,
				config: event.config,
				validation: event.validation ?? null
			};
		} else if (event.type === 'blend_config') {
			this.callbacks.onBlendConfig?.(event.config, event.validation ?? null);
			if (event.run_id && event.jobs?.length) {
				this.pendingSubmission = { kind: 'blend', runId: event.run_id, jobs: event.jobs };
			}
		} else if (event.type === 'done' || event.type === 'stopped') {
			this.messages = [...this.messages, mergeArtifacts(this.streamingTurn, event.turn)];
			this.streamingTurn = null;
			if (this.pendingSubmission) {
				if (this.pendingSubmission.kind === 'benchmark') {
					this.callbacks.onBenchmarkSubmitted?.(
						this.pendingSubmission.runId,
						this.pendingSubmission.jobs,
						activeSessionId
					);
				} else {
					this.callbacks.onBlendSubmitted?.(
						this.pendingSubmission.runId,
						this.pendingSubmission.jobs,
						activeSessionId
					);
				}
				this.pendingSubmission = null;
			}
			this.sessions = this.sessions
				.map((s) =>
					s.id === activeSessionId
						? {
								...s,
								message_count: s.message_count + 2,
								updated_at: new Date().toISOString(),
								scope
							}
						: s
				)
				.sort(byUpdatedAt);
		}
	}

	approveSubmit = async () => {
		if (!this.sessionId || !this.pendingApproval) return;
		const approval = this.pendingApproval;
		this.pendingApproval = null;
		try {
			if (approval.kind === 'benchmark') {
				const response = await submitChatBenchmark(this.sessionId, {
					tool_call_id: approval.toolCallId,
					approved_config: approval.config
				});
				this.callbacks.onBenchmarkConfig?.(
					response.benchmark_config,
					response.benchmark_validation
				);
				this.callbacks.onBenchmarkSubmitted?.(response.run_id, response.jobs, this.sessionId);
				void this.refreshJobActivity();
			} else {
				const response = await submitChatBlend(this.sessionId, {
					tool_call_id: approval.toolCallId,
					approved_config: approval.config
				});
				this.callbacks.onBlendConfig?.(response.blend_config, response.blend_validation);
				this.callbacks.onBlendSubmitted?.(response.run_id, response.jobs, this.sessionId);
				void this.refreshJobActivity();
			}
		} catch (e) {
			this.error = (e as Error).message ?? 'Submit failed.';
		}
	};

	declineSubmit = async () => {
		if (!this.sessionId || !this.pendingApproval) return;
		const approval = this.pendingApproval;
		this.pendingApproval = null;
		try {
			if (approval.kind === 'benchmark') {
				await denyChatBenchmarkApproval(this.sessionId, {
					tool_call_id: approval.toolCallId,
					approved_config: approval.config,
					message: 'The user wants to revise the benchmark before running it.'
				});
			} else {
				await denyChatBlendApproval(this.sessionId, {
					tool_call_id: approval.toolCallId,
					approved_config: approval.config,
					message: 'The user wants to revise the blend before training it.'
				});
			}
		} catch (e) {
			this.error = (e as Error).message ?? 'Approval update failed.';
		}
	};
}
