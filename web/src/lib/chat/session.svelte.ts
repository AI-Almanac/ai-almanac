import {
	createChatSession,
	getChatSessions,
	getChatSession,
	updateChatSession,
	deleteChatSession,
	sendChatMessage,
	submitChatBenchmark,
	denyChatBenchmarkApproval,
	submitChatBlend,
	denyChatBlendApproval,
	type BenchmarkRunSpec,
	type BenchmarkValidation,
	type Blend,
	type BlendRunSpec,
	type BlendValidation,
	type ChatEvent,
	type ChatMessage,
	type ChatScope,
	type ChatSession,
	type Job
} from '$lib/api';

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

function chatErrorMessage(event: Extract<ChatEvent, { type: 'error' }>): string {
	if (event.error_type === 'provider_error') return 'The chat provider failed. Please try again.';
	if (event.error_type === 'tool_error') return 'A benchmark data lookup failed. Please try again.';
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
			const session = await createChatSession(scope, scope.title ?? undefined);
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

		try {
			const scope = this.getScope();
			for await (const event of sendChatMessage(activeSessionId, text, scope)) {
				this.applyStreamEvent(event, activeSessionId, scope);
			}
		} catch (e) {
			const message = (e as Error).message ?? 'An error occurred.';
			this.streamingTurn = null;
			await this.loadSession(activeSessionId);
			this.error = message;
		} finally {
			this.sending = false;
			this.sendLocked = false;
		}
	}

	private startTurn(turnId: string): ChatMessage {
		return {
			id: turnId,
			role: 'assistant',
			content: '',
			created_at: new Date().toISOString(),
			tool_calls: [],
			artifacts: []
		};
	}

	private applyStreamEvent(event: ChatEvent, activeSessionId: string, scope: ChatScope) {
		if (event.type === 'text_delta') {
			if (!this.streamingTurn || this.streamingTurn.id !== event.turn_id) {
				this.streamingTurn = this.startTurn(event.turn_id);
			}
			this.streamingTurn = {
				...this.streamingTurn,
				content: this.streamingTurn.content + event.content
			};
		} else if (event.type === 'tool_call') {
			if (!this.streamingTurn || this.streamingTurn.id !== event.turn_id) {
				this.streamingTurn = this.startTurn(event.turn_id);
			}
			this.streamingTurn = {
				...this.streamingTurn,
				tool_calls: [...(this.streamingTurn.tool_calls ?? []), event.tool_call]
			};
		} else if (event.type === 'tool_result') {
			if (!this.streamingTurn || this.streamingTurn.id !== event.turn_id) return;
			const createdJob = jobFromToolResult(event.result);
			if (createdJob) this.callbacks.onJobsCreated?.([createdJob]);
			this.streamingTurn = {
				...this.streamingTurn,
				tool_calls: (this.streamingTurn.tool_calls ?? []).map((tc) =>
					tc.id === event.tool_call_id ? { ...tc, status: event.status, result: event.result } : tc
				)
			};
		} else if (event.type === 'artifact') {
			if (!this.streamingTurn || this.streamingTurn.id !== event.turn_id) return;
			this.streamingTurn = {
				...this.streamingTurn,
				artifacts: [...(this.streamingTurn.artifacts ?? []), event.artifact],
				tool_calls: (this.streamingTurn.tool_calls ?? []).map((tc) =>
					tc.id === event.tool_call_id
						? { ...tc, artifacts: [...(tc.artifacts ?? []), event.artifact] }
						: tc
				)
			};
		} else if (event.type === 'error') {
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
			} else {
				const response = await submitChatBlend(this.sessionId, {
					tool_call_id: approval.toolCallId,
					approved_config: approval.config
				});
				this.callbacks.onBlendConfig?.(response.blend_config, response.blend_validation);
				this.callbacks.onBlendSubmitted?.(response.run_id, response.jobs, this.sessionId);
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
