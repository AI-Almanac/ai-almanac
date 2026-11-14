import { failureMessage } from '$lib/api/core';
import {
	createChatSession,
	getChatSessions,
	getChatSession,
	updateChatSession,
	deleteChatSession,
	getChatJobActivity,
	sendChatMessage,
	decideChatToolApproval,
	type BenchmarkRunSpec,
	type BenchmarkValidation,
	type Blend,
	type BlendRunSpec,
	type BlendValidation,
	type ChatApprovalRequest,
	type ChatEvent,
	type ChatJobActivity,
	type ChatMessage,
	type ChatScope,
	type ChatSession,
	type Job
} from '$lib/api';
import {
	NOT_APPROVED,
	approvalFromRequest,
	isApprovalRequest,
	withAwaitingApproval,
	type PendingApproval
} from '$lib/chat/approval';
import { actionFromToolCall, type InterfaceAction } from '$lib/interface-actions/actions';
import { emptyAssistantTurn, foldTurnEvent, startsTurn, withToolResult } from '$lib/chat/turn';

export interface ChatSessionCallbacks {
	onSessionReady?: (sessionId: string) => void;
	onJobsCreated?: (jobs: Job[]) => void;
	onBenchmarkConfig?: (config: BenchmarkRunSpec, validation?: BenchmarkValidation | null) => void;
	onBenchmarkSubmitted?: (runId: string, jobs: Job[], sessionId: string | null) => void;
	onBlendConfig?: (config: BlendRunSpec, validation?: BlendValidation | null) => void;
	onBlendSubmitted?: (runId: string, jobs: Blend[], sessionId: string | null) => void;
	/** The assistant asked the interface to act, in a reply that is streaming now. */
	onInterfaceAction?: (action: InterfaceAction) => void;
}

/** What the assistant is told when the user declines its card, by what the card proposed. */
const DECLINED: Record<PendingApproval['kind'], string> = {
	step: 'The user declined this step.',
	benchmark: 'The user wants to revise the benchmark before running it.',
	blend: 'The user wants to revise the blend before training it.'
};

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

function chatErrorMessage(event: Extract<ChatEvent, { type: 'error' }>): string {
	if (event.error_type === 'provider_error') return 'The chat provider failed. Please try again.';
	if (event.error_type === 'tool_error') return 'A benchmark data lookup failed. Please try again.';
	// The locally run model is paused for a forecast or still loading; the
	// server's message says which.
	if (event.error_type === 'assistant_unavailable') return event.message;
	if (event.error_type === 'scope_mismatch') {
		return 'This chat belongs to a different benchmark view. Start a new chat for this benchmark.';
	}
	if (event.error_type === 'session_busy') {
		return 'The assistant is still replying in this chat. Wait for it to finish, then send again.';
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
	jobActivity = $state<ChatJobActivity>({ events: [], active: 0 });
	sending = $state(false);
	error = $state<string | null>(null);
	loadingSession = $state(false);
	pendingSubmission = $state<
		| { kind: 'benchmark'; runId: string; jobs: Job[] }
		| { kind: 'blend'; runId: string; jobs: Blend[] }
		| null
	>(null);
	pendingApproval = $state<PendingApproval | null>(null);

	readonly visibleTurns = $derived(
		withAwaitingApproval(
			this.streamingTurn ? [...this.messages, this.streamingTurn] : this.messages,
			this.pendingApproval?.toolCallId
		)
	);
	readonly currentSession = $derived(this.sessions.find((s) => s.id === this.sessionId));

	private sendLocked = false;
	private loadedScopeToken: string | null = null;

	constructor(
		private getScope: () => ChatScope,
		private callbacks: ChatSessionCallbacks = {}
	) {}

	get canSend(): boolean {
		return !this.sending && !this.sendLocked && !this.loadingSession;
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
			this.messages = detail.transcript;
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
			this.pendingApproval = null;
			if (detail.pending_approval) this.askForApproval(detail.pending_approval);
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
			this.pendingApproval = null;
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
		this.error = null;
		this.pendingSubmission = null;
		this.leaveApprovalUnanswered();

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

		try {
			const scope = this.getScope();
			for await (const event of sendChatMessage(activeSessionId, text, scope)) {
				this.applyStreamEvent(event, activeSessionId, scope);
			}
		} catch (e) {
			const message = failureMessage(e);
			this.streamingTurn = null;
			await this.loadSession(activeSessionId);
			this.error = message;
		} finally {
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

	/** Show the card for a run the assistant wants to start, live or on a reloaded conversation. */
	private askForApproval(request: ChatApprovalRequest) {
		if (request.type === 'benchmark_approval_request') {
			this.callbacks.onBenchmarkConfig?.(request.config, request.validation ?? null);
		} else if (request.type === 'blend_approval_request') {
			this.callbacks.onBlendConfig?.(request.config, request.validation ?? null);
		}
		this.pendingApproval = approvalFromRequest(request);
	}

	private markNotRun(toolCallId: string) {
		this.messages = withToolResult(this.messages, toolCallId, NOT_APPROVED, 'failed');
	}

	/** Writing a new message instead of answering the card leaves its step not run. */
	private leaveApprovalUnanswered() {
		if (this.pendingApproval) this.markNotRun(this.pendingApproval.toolCallId);
		this.pendingApproval = null;
	}

	private applyStreamEvent(
		event: ChatEvent,
		activeSessionId: string,
		scope: ChatScope,
		addedTurns = 2
	) {
		if (event.type === 'assistant_status') {
			this.assistantWaking = event.status === 'waking';
			return;
		}
		this.assistantWaking = false;
		if (startsTurn(event) && this.streamingTurn?.id !== event.turn_id) {
			this.streamingTurn = emptyAssistantTurn(event.turn_id);
		}
		if ('turn_id' in event && this.streamingTurn?.id === event.turn_id) {
			this.streamingTurn = foldTurnEvent(this.streamingTurn, event);
		}
		if (event.type === 'tool_result') {
			const createdJob = jobFromToolResult(event.result);
			if (createdJob) this.callbacks.onJobsCreated?.([createdJob]);
			// Only here, as the reply streams: a reloaded conversation leaves its actions as buttons.
			const call = this.streamingTurn?.tool_calls?.find((tc) => tc.id === event.tool_call_id);
			const action = call ? actionFromToolCall({ name: call.name, result: event.result }) : null;
			if (action) this.callbacks.onInterfaceAction?.(action);
			const inStreamingTurn = this.streamingTurn?.tool_calls?.some(
				(tc) => tc.id === event.tool_call_id
			);
			// An approved step reports back in the turn that continues after it.
			if (!inStreamingTurn) {
				this.messages = withToolResult(
					this.messages,
					event.tool_call_id,
					event.result,
					event.status
				);
			}
		}

		if (event.type === 'error') {
			throw new Error(chatErrorMessage(event));
		} else if (isApprovalRequest(event)) {
			this.askForApproval(event);
		} else if (event.type === 'benchmark_config') {
			this.callbacks.onBenchmarkConfig?.(event.config, event.validation ?? null);
			if (event.run_id && event.jobs?.length) {
				this.pendingSubmission = { kind: 'benchmark', runId: event.run_id, jobs: event.jobs };
			}
		} else if (event.type === 'blend_config') {
			this.callbacks.onBlendConfig?.(event.config, event.validation ?? null);
			if (event.run_id && event.jobs?.length) {
				this.pendingSubmission = { kind: 'blend', runId: event.run_id, jobs: event.jobs };
			}
		} else if (event.type === 'done') {
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
								message_count: s.message_count + addedTurns,
								updated_at: new Date().toISOString(),
								scope
							}
						: s
				)
				.sort(byUpdatedAt);
		}
	}

	approveSubmit = () => this.answerApproval(true);
	declineSubmit = () => this.answerApproval(false);

	/** Answer the card on show; the assistant carries on from the decision as a turn of its own. */
	private async answerApproval(approved: boolean) {
		if (!this.sessionId || !this.pendingApproval) return;
		const approval = this.pendingApproval;
		this.pendingApproval = null;
		if (!approved) this.markNotRun(approval.toolCallId);
		try {
			const message = approved ? undefined : DECLINED[approval.kind];
			await this.decideStep(approval.toolCallId, approved, message);
		} catch (e) {
			this.error = failureMessage(e);
		}
	}

	/** Decide a paused step, then stream the assistant carrying on from it as its own turn. */
	private async decideStep(toolCallId: string, approved: boolean, message?: string) {
		const sessionId = this.sessionId!;
		const scope = this.getScope();
		this.sending = true;
		this.sendLocked = true;
		try {
			for await (const event of decideChatToolApproval(sessionId, {
				tool_call_id: toolCallId,
				approved,
				message
			})) {
				this.applyStreamEvent(event, sessionId, scope, 1);
			}
		} catch (e) {
			this.streamingTurn = null;
			await this.loadSession(sessionId);
			throw e;
		} finally {
			this.sending = false;
			this.sendLocked = false;
			// An approved step can start a run.
			void this.refreshJobActivity();
		}
	}
}
