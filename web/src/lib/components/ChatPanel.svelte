<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';
	import FigureCard from '$lib/components/FigureCard.svelte';
	import FigureLightbox from '$lib/components/FigureLightbox.svelte';
	import type { ParsedFigure } from '$lib/result-parser';
	import {
		createChatSession,
		getChatSessions,
		getChatSession,
		updateChatSession,
		deleteChatSession,
		sendChatMessage,
		type ChatSession,
		type ChatMessage,
		type ChatEvent,
		type ChatScope,
		type ChatToolCall,
		type ChatArtifact,
		type BenchmarkRunSpec,
		type BenchmarkValidation
	} from '$lib/api';

	import type { Job } from '$lib/api';

	type GalleryFigure = {
		artifactId: string;
		figure: ParsedFigure;
		toolName: string | null;
		code: string | null;
		createdAt: string;
	};

	function renderMarkdown(text: string): string {
		return DOMPurify.sanitize(marked.parse(text) as string);
	}

	interface Props {
		jobs?: Job[];
		scopeKey: string;
		scopeKind?: ChatScope['kind'];
		preferredSessionId?: string | null;
		title?: string | null;
		emptyMessage?: string;
		placeholder?: string;
		suggestions?: string[];
		initialMessage?: string;
		externalPrompt?: string | null;
		externalPromptNonce?: number;
		showArtifacts?: boolean;
		onSessionReady?: (sessionId: string) => void;
		onJobsCreated?: (jobs: Job[]) => void;
		onBenchmarkConfig?: (config: BenchmarkRunSpec, validation?: BenchmarkValidation | null) => void;
		onBenchmarkSubmitted?: (runId: string, jobs: Job[], sessionId: string | null) => void;
	}

	let {
		jobs = [],
		scopeKey,
		scopeKind = 'benchmark_run_group',
		preferredSessionId = null,
		title = null,
		emptyMessage = 'Ask a question about the benchmark results above.',
		placeholder = 'Ask about the results… (Enter to send, Shift+Enter for newline)',
		suggestions = [
			'How do the models compare on false alarm rate?',
			'Which model has the best MAE at longer lead times?',
			'Summarise the key findings from these runs.'
		],
		initialMessage = '',
		externalPrompt = null,
		externalPromptNonce = 0,
		showArtifacts = true,
		onSessionReady,
		onJobsCreated,
		onBenchmarkConfig,
		onBenchmarkSubmitted
	}: Props = $props();

	// Session list
	let sessions = $state<ChatSession[]>([]);
	let sessionId = $state<string | null>(null);
	let messages = $state<ChatMessage[]>([]);
	let input = $state('');
	let sending = $state(false);
	let error = $state<string | null>(null);
	let loadingSession = $state(false);
	let showSessionList = $state(false);
	let renamingSessionId = $state<string | null>(null);
	let renamingValue = $state('');
	let savingTitle = $state(false);
	let sendLocked = false;
	let initialMessageHandled = $state(false);
	let handledExternalPromptNonce = $state(0);
	let pendingSubmission = $state<{ runId: string; jobs: Job[] } | null>(null);
	let pendingApproval = $state<{
		toolCallId: string;
		config: BenchmarkRunSpec;
		validation: BenchmarkValidation | null;
	} | null>(null);

	// Streaming assistant turn
	let streamingTurn = $state<ChatMessage | null>(null);
	let shownCode = $state<Set<string>>(new Set());
	let loadedScopeToken = $state<string | null>(null);
	let galleryFigures = $derived(sessionFigures());
	let activeTab = $state<'chat' | 'artifacts'>('chat');

	function toggleCode(key: string) {
		const next = new Set(shownCode);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		shownCode = next;
	}

	const CODE_TOOLS = new Set(['run_code_sandbox', 'run_code']);

	async function copyCode(code: string) {
		await navigator.clipboard.writeText(code);
	}

	function titleCase(value: string): string {
		return value
			.split(/[\s_-]+/)
			.filter(Boolean)
			.map((part) => part[0].toUpperCase() + part.slice(1).toLowerCase())
			.join(' ');
	}

	function formatDateForTitle(value?: string): string | null {
		if (!value) return null;
		const date = new Date(`${value}T00:00:00`);
		if (Number.isNaN(date.getTime())) return null;
		return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
	}

	function defaultSessionTitle(scope: Pick<ChatScope, 'key'>): string {
		if (title) return title;
		if (scopeKind === 'benchmark_setup') return 'Benchmark setup';
		const firstJob = jobs[0];
		const eventType = titleCase(firstJob?.params?.event_type ?? 'benchmark');
		const region = titleCase(firstJob?.params?.region ?? scope.key);
		const start = formatDateForTitle(firstJob?.params?.start_date);
		const end = formatDateForTitle(firstJob?.params?.end_date);
		const dateRange = start && end ? `${start} to ${end}` : (start ?? end);
		const modelCount = jobs.length;
		const modelLabel = `${modelCount} model${modelCount === 1 ? '' : 's'}`;
		return dateRange
			? `${eventType} · ${region} · ${modelLabel} · ${dateRange}`
			: `${eventType} · ${region} · ${modelLabel}`;
	}

	function sessionScope(): ChatScope {
		const scope = {
			kind: scopeKind,
			key: scopeKey,
			job_ids: jobs.map((j) => j.id)
		} satisfies Omit<ChatScope, 'title'>;
		return { ...scope, title: defaultSessionTitle(scope) };
	}

	function scopeToken(scope: ChatScope): string {
		const jobIds = [...scope.job_ids].sort().join(',');
		return `${scope.kind}:${scope.key}:${jobIds}`;
	}

	async function refreshSessionsForScope() {
		const scope = sessionScope();
		const all = await getChatSessions(scope);
		sessions = all;

		if (preferredSessionId && !all.some((session) => session.id === preferredSessionId)) {
			try {
				await loadSession(preferredSessionId);
				return;
			} catch {
				// Fall back to scope sessions below; the preferred setup chat may no longer exist.
			}
		}

		const nextSessionId =
			preferredSessionId && all.some((session) => session.id === preferredSessionId)
				? preferredSessionId
				: sessionId && all.some((session) => session.id === sessionId)
					? sessionId
					: (all[0]?.id ?? null);

		if (nextSessionId) {
			await loadSession(nextSessionId);
			return;
		}
		await createNewSession();
	}

	function codeForToolCall(toolCall: ChatToolCall): string | null {
		if (!CODE_TOOLS.has(toolCall.name)) return null;
		const code = toolCall.input.code;
		return typeof code === 'string' && code.length > 0 ? code : null;
	}

	function mergeArtifacts(base: ChatMessage | null, incoming: ChatMessage): ChatMessage {
		const byId = new Map<string, NonNullable<ChatMessage['artifacts']>[number]>();
		for (const artifact of base?.artifacts ?? []) byId.set(artifact.id, artifact);
		for (const artifact of incoming.artifacts ?? []) byId.set(artifact.id, artifact);
		return { ...incoming, artifacts: [...byId.values()] };
	}

	function artifactsForTurn(turn: ChatMessage): NonNullable<ChatMessage['artifacts']> {
		const byId = new Map<string, NonNullable<ChatMessage['artifacts']>[number]>();
		for (const artifact of turn.artifacts ?? []) byId.set(artifact.id, artifact);
		for (const toolCall of turn.tool_calls ?? []) {
			for (const artifact of toolCall.artifacts ?? []) {
				byId.set(artifact.id, artifact);
			}
		}
		return [...byId.values()];
	}

	function visibleTurns(): ChatMessage[] {
		return streamingTurn ? [...messages, streamingTurn] : messages;
	}

	function artifactToFigure(artifact: ChatArtifact): ParsedFigure {
		const name = artifact.filename ?? `${artifact.id}.webp`;
		return {
			raw: {
				name,
				type: 'figure',
				url: artifact.url
			},
			kind: 'unknown',
			metric: null,
			model: null,
			window: null,
			label: artifact.label ?? name
		};
	}

	function jobFromToolResult(result: unknown): Job | null {
		if (!result || typeof result !== 'object' || !('job' in result)) return null;
		const job = (result as { job?: unknown }).job;
		if (!job || typeof job !== 'object' || !('id' in job)) return null;
		return job as Job;
	}

	function chatErrorMessage(event: Extract<ChatEvent, { type: 'error' }>): string {
		if (event.error_type === 'provider_error') return 'The chat provider failed. Please try again.';
		if (event.error_type === 'tool_error')
			return 'A benchmark data lookup failed. Please try again.';
		if (event.error_type === 'scope_mismatch') {
			return 'This chat belongs to a different benchmark view. Start a new chat for this benchmark.';
		}
		return event.message || 'Chat request failed.';
	}

	function sessionFigures(): GalleryFigure[] {
		const byId = new Map<string, GalleryFigure>();
		for (const turn of visibleTurns()) {
			if (turn.role !== 'assistant') continue;
			const codeTools = (turn.tool_calls ?? []).filter((toolCall) => codeForToolCall(toolCall));
			for (const artifact of artifactsForTurn(turn)) {
				let sourceTool: ChatToolCall | null = null;
				for (const toolCall of turn.tool_calls ?? []) {
					if ((toolCall.artifacts ?? []).some((toolArtifact) => toolArtifact.id === artifact.id)) {
						sourceTool = toolCall;
						break;
					}
				}
				if (!sourceTool && codeTools.length === 1) {
					sourceTool = codeTools[0];
				}
				byId.set(artifact.id, {
					artifactId: artifact.id,
					figure: artifactToFigure(artifact),
					toolName: sourceTool?.name ?? null,
					code: sourceTool ? codeForToolCall(sourceTool) : null,
					createdAt: artifact.created_at
				});
			}
		}
		return [...byId.values()];
	}

	let messagesEl = $state<HTMLElement | null>(null);
	let selectedFigureIndex = $state<number | null>(null);

	function artifactCodeKey(artifactId: string): string {
		return `artifact-${artifactId}`;
	}

	function openArtifactInGallery(artifactId: string) {
		const idx = galleryFigures.findIndex((f) => f.artifactId === artifactId);
		if (idx !== -1) selectedFigureIndex = idx;
	}

	function handleOutsideClick(e: MouseEvent) {
		if (showSessionList && !(e.target as Element).closest('.session-selector')) {
			showSessionList = false;
		}
	}

	onMount(() => {
		document.addEventListener('click', handleOutsideClick, true);
		return () => document.removeEventListener('click', handleOutsideClick, true);
	});

	$effect(() => {
		if (!showArtifacts && activeTab === 'artifacts') activeTab = 'chat';
	});

	$effect(() => {
		const scope = sessionScope();
		const token = scopeToken(scope);
		if (!scope.key || token === loadedScopeToken) return;
		loadedScopeToken = token;
		streamingTurn = null;
		void (async () => {
			try {
				await refreshSessionsForScope();
			} catch {
				error = 'Failed to load chat sessions.';
			}
		})();
	});

	$effect(() => {
		if (!sessionId || sending || initialMessageHandled || !initialMessage.trim()) return;
		initialMessageHandled = true;
		void submit(initialMessage.trim());
	});

	$effect(() => {
		if (
			!sessionId ||
			sending ||
			!externalPrompt?.trim() ||
			externalPromptNonce === handledExternalPromptNonce
		) {
			return;
		}
		handledExternalPromptNonce = externalPromptNonce;
		void submit(externalPrompt.trim());
	});

	async function loadSession(id: string) {
		loadingSession = true;
		error = null;
		try {
			const detail = await getChatSession(id);
			sessionId = id;
			onSessionReady?.(id);
			messages = detail.transcript;
			if (detail.benchmark_config) {
				onBenchmarkConfig?.(detail.benchmark_config, detail.benchmark_validation ?? null);
			}
			sessions = sessions.map((session) =>
				session.id === id ? { ...session, scope: detail.scope } : session
			);
		} catch {
			error = 'Failed to load session.';
		} finally {
			loadingSession = false;
			showSessionList = false;
		}
	}

	async function createNewSession(): Promise<string | null> {
		error = null;
		try {
			const scope = sessionScope();
			const session = await createChatSession(scope, scope.title ?? undefined);
			if (session.benchmark_config) {
				onBenchmarkConfig?.(session.benchmark_config, session.benchmark_validation ?? null);
			}
			sessions = [session, ...sessions].sort(
				(a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
			);
			sessionId = session.id;
			onSessionReady?.(session.id);
			messages = [];
			loadedScopeToken = scopeToken(scope);
			return session.id;
		} catch {
			error = 'Failed to create session.';
		}
		showSessionList = false;
		return null;
	}

	function beginRename(session: ChatSession, e?: MouseEvent) {
		e?.stopPropagation();
		renamingSessionId = session.id;
		renamingValue = session.title ?? '';
		showSessionList = false;
	}

	function cancelRename() {
		renamingSessionId = null;
		renamingValue = '';
	}

	async function saveRename() {
		if (!renamingSessionId || savingTitle) return;
		savingTitle = true;
		error = null;
		try {
			const updated = await updateChatSession(renamingSessionId, {
				title: renamingValue.trim() || null
			});
			sessions = sessions
				.map((s) => (s.id === updated.id ? updated : s))
				.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
			cancelRename();
		} catch {
			error = 'Failed to rename session.';
		} finally {
			savingTitle = false;
		}
	}

	function handleRenameKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			void saveRename();
			return;
		}
		if (e.key === 'Escape') {
			e.preventDefault();
			cancelRename();
		}
	}

	async function handleDeleteSession(id: string, e: MouseEvent) {
		e.stopPropagation();
		try {
			await deleteChatSession(id);
			const nextSessions = sessions.filter((s) => s.id !== id);
			sessions = nextSessions;
			if (sessionId === id) {
				if (nextSessions.length > 0) {
					await loadSession(nextSessions[0].id);
				} else {
					await createNewSession();
				}
			}
		} catch {
			error = 'Failed to delete session.';
		}
	}

	function currentSession(): ChatSession | undefined {
		return sessions.find((s) => s.id === sessionId);
	}

	function sessionLabel(s: ChatSession): string {
		return s.title || s.scope.title || `Chat ${new Date(s.created_at).toLocaleDateString()}`;
	}

	// Scroll to bottom whenever messages update.
	$effect(() => {
		messages;
		streamingTurn;
		tick().then(() => {
			if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
		});
	});

	async function submit(overrideText?: string) {
		const text = (overrideText ?? input).trim();
		if (!text || sending || sendLocked || loadingSession) return;
		sendLocked = true;
		const activeSessionId = sessionId ?? (await createNewSession());
		if (!activeSessionId) {
			sendLocked = false;
			return;
		}
		if (!overrideText) input = '';
		sending = true;
		error = null;
		pendingSubmission = null;
		pendingApproval = null;

		messages = [
			...messages,
			{
				id: crypto.randomUUID(),
				role: 'user',
				content: text,
				created_at: new Date().toISOString(),
				tool_calls: []
			}
		];

		try {
			const scope = sessionScope();
			for await (const event of sendChatMessage(activeSessionId, text, scope)) {
				if (event.type === 'text_delta') {
					if (!streamingTurn || streamingTurn.id !== event.turn_id) {
						streamingTurn = {
							id: event.turn_id,
							role: 'assistant',
							content: '',
							created_at: new Date().toISOString(),
							tool_calls: [],
							artifacts: []
						};
					}
					streamingTurn = { ...streamingTurn, content: streamingTurn.content + event.content };
				} else if (event.type === 'tool_call') {
					if (!streamingTurn || streamingTurn.id !== event.turn_id) {
						streamingTurn = {
							id: event.turn_id,
							role: 'assistant',
							content: '',
							created_at: new Date().toISOString(),
							tool_calls: [],
							artifacts: []
						};
					}
					streamingTurn = {
						...streamingTurn,
						tool_calls: [...(streamingTurn.tool_calls ?? []), event.tool_call]
					};
				} else if (event.type === 'tool_result') {
					if (!streamingTurn || streamingTurn.id !== event.turn_id) continue;
					const createdJob = jobFromToolResult(event.result);
					if (createdJob) onJobsCreated?.([createdJob]);
					streamingTurn = {
						...streamingTurn,
						tool_calls: (streamingTurn.tool_calls ?? []).map((tc) =>
							tc.id === event.tool_call_id
								? { ...tc, status: event.status, result: event.result }
								: tc
						)
					};
				} else if (event.type === 'artifact') {
					if (!streamingTurn || streamingTurn.id !== event.turn_id) continue;
					streamingTurn = {
						...streamingTurn,
						artifacts: [...(streamingTurn.artifacts ?? []), event.artifact],
						tool_calls: (streamingTurn.tool_calls ?? []).map((tc) =>
							tc.id === event.tool_call_id
								? { ...tc, artifacts: [...(tc.artifacts ?? []), event.artifact] }
								: tc
						)
					};
				} else if (event.type === 'error') {
					throw new Error(chatErrorMessage(event));
				} else if (event.type === 'benchmark_approval_request') {
					onBenchmarkConfig?.(event.config, event.validation ?? null);
					pendingApproval = {
						toolCallId: event.tool_call_id,
						config: event.config,
						validation: event.validation ?? null
					};
				} else if (event.type === 'benchmark_config') {
					onBenchmarkConfig?.(event.config, event.validation ?? null);
					if (event.run_id && event.jobs?.length) {
						pendingSubmission = { runId: event.run_id, jobs: event.jobs };
					}
				} else if (event.type === 'done') {
					messages = [...messages, mergeArtifacts(streamingTurn, event.turn)];
					streamingTurn = null;
					if (pendingSubmission) {
						onBenchmarkSubmitted?.(
							pendingSubmission.runId,
							pendingSubmission.jobs,
							activeSessionId
						);
						pendingSubmission = null;
					}
					// Update session's message_count in the list
					sessions = sessions
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
						.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
				}
			}
		} catch (e: any) {
			const message = e.message ?? 'An error occurred.';
			streamingTurn = null;
			await loadSession(activeSessionId);
			error = message;
		} finally {
			sending = false;
			sendLocked = false;
		}
	}

	async function approveSubmit() {
		if (!sessionId || !pendingApproval) return;
		const approval = pendingApproval;
		pendingApproval = null;
		const { submitChatBenchmark } = await import('$lib/api');
		try {
			const response = await submitChatBenchmark(sessionId, {
				tool_call_id: approval.toolCallId,
				approved_config: approval.config
			});
			onBenchmarkConfig?.(response.benchmark_config, response.benchmark_validation);
			onBenchmarkSubmitted?.(response.run_id, response.jobs, sessionId);
		} catch (e: any) {
			error = e.message ?? 'Benchmark submit failed.';
		}
	}

	async function declineSubmit() {
		if (!sessionId || !pendingApproval) return;
		const approval = pendingApproval;
		pendingApproval = null;
		const { denyChatBenchmarkApproval } = await import('$lib/api');
		try {
			await denyChatBenchmarkApproval(sessionId, {
				tool_call_id: approval.toolCallId,
				approved_config: approval.config,
				message: 'The user wants to revise the benchmark before running it.'
			});
		} catch (e: any) {
			error = e.message ?? 'Benchmark approval update failed.';
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			submit();
		}
	}

	let copyState = $state<'idle' | 'copied'>('idle');

	async function copyChat() {
		if (messages.length === 0) return;
		const text = messages
			.map((m) => `${m.role === 'user' ? 'You' : 'AI'}: ${m.content}`)
			.join('\n\n');
		await navigator.clipboard.writeText(text);
		copyState = 'copied';
		setTimeout(() => (copyState = 'idle'), 1500);
	}

	const TOOL_LABELS: Record<string, string> = {
		list_regions: 'checking regions',
		list_datasets: 'checking datasets',
		list_models: 'checking models',
		get_benchmark_config: 'reading benchmark plan',
		update_benchmark_config: 'updating benchmark plan',
		validate_benchmark_config: 'validating benchmark plan',
		submit_benchmark: 'submitting benchmark',
		list_jobs: 'listing jobs',
		list_failed_jobs: 'checking failed jobs',
		get_job_info: 'fetching job info',
		get_job_logs: 'reading job logs',
		rerun_job: 'rerunning job',
		get_job_metrics: 'loading metrics',
		get_spatial_summary: 'loading spatial summary',
		run_code_sandbox: 'running sandbox computation',
		run_code: 'running custom analysis'
	};

	function formatToolName(name: string): string {
		return TOOL_LABELS[name] ?? name.replace(/_/g, ' ');
	}
</script>

<div class="chat-panel">
	<!-- Header with session picker -->
	<div class="chat-header">
		<span class="ai-badge">✦ AI</span>
		<div class="session-selector">
			{#if currentSession() && renamingSessionId === currentSession()!.id}
				<div class="session-rename-bar">
					<input
						class="session-rename-input"
						bind:value={renamingValue}
						onkeydown={handleRenameKeydown}
						placeholder={sessionLabel(currentSession()!)}
						maxlength="140"
					/>
					<button
						class="session-action-btn"
						onclick={() => void saveRename()}
						disabled={savingTitle}
					>
						{savingTitle ? 'Saving...' : 'Save'}
					</button>
					<button
						class="session-action-btn secondary"
						onclick={cancelRename}
						disabled={savingTitle}
					>
						Cancel
					</button>
				</div>
			{:else}
				<button
					class="session-current"
					onclick={() => {
						showSessionList = !showSessionList;
					}}
					title="Switch session"
				>
					<span class="session-label">
						{#if currentSession()}
							{sessionLabel(currentSession()!)}
						{:else}
							AI Analysis
						{/if}
					</span>
					<span class="session-chevron" class:open={showSessionList}>▾</span>
				</button>
			{/if}

			{#if showSessionList}
				<div class="session-dropdown">
					<button class="session-new-btn" onclick={createNewSession}> + New Chat </button>
					{#if sessions.length > 0}
						<div class="session-divider"></div>
						{#each sessions as s}
							<div class="session-item" class:active={s.id === sessionId}>
								<button class="session-item-btn" onclick={() => loadSession(s.id)}>
									<span class="session-item-title">{sessionLabel(s)}</span>
									<span class="session-item-meta"
										>{s.message_count} msg · {new Date(s.updated_at).toLocaleDateString()}</span
									>
								</button>
								<button
									class="session-item-rename"
									title="Rename"
									onclick={(e) => beginRename(s, e)}>Rename</button
								>
								<button
									class="session-item-delete"
									title="Delete"
									onclick={(e) => handleDeleteSession(s.id, e)}>&times;</button
								>
							</div>
						{/each}
					{/if}
				</div>
			{/if}
		</div>

		<div class="header-actions">
			{#if currentSession() && renamingSessionId !== currentSession()!.id}
				<button
					class="copy-btn"
					onclick={(e) => beginRename(currentSession()!, e)}
					title="Rename chat"
				>
					Rename
				</button>
			{/if}
			<button
				class="copy-btn"
				onclick={copyChat}
				disabled={messages.length === 0}
				title="Copy chat to clipboard"
			>
				{copyState === 'copied' ? '✓ Copied' : 'Copy'}
			</button>
		</div>
	</div>

	<div class="panel-tabs">
		<button
			class="panel-tab"
			class:active={activeTab === 'chat'}
			onclick={() => {
				activeTab = 'chat';
			}}
		>
			Chat
		</button>
		{#if showArtifacts}
			<button
				class="panel-tab"
				class:active={activeTab === 'artifacts'}
				onclick={() => {
					activeTab = 'artifacts';
				}}
			>
				Artifacts
				{#if galleryFigures.length > 0}
					<span class="panel-tab-count">{galleryFigures.length}</span>
				{/if}
			</button>
		{/if}
	</div>

	{#if activeTab === 'chat'}
		<div class="messages" bind:this={messagesEl}>
			{#if loadingSession}
				<div class="loading-msgs">
					<div class="spinner-sm"></div>
					<span>Loading…</span>
				</div>
			{:else if messages.length === 0 && !sending}
				<div class="empty-chat">
					<p>{emptyMessage}</p>
					<div class="suggestions">
						{#each suggestions as suggestion}
							<button
								class="suggestion"
								onclick={() => {
									input = suggestion;
									submit();
								}}
							>
								{suggestion}
							</button>
						{/each}
					</div>
				</div>
			{/if}

			{#each visibleTurns() as msg, i}
				{#if msg.role === 'user' || msg.content}
					<div class="message {msg.role}">
						<div class="message-content" class:prose={msg.role === 'assistant'}>
							{#if msg.role === 'assistant'}
								{@html renderMarkdown(msg.content)}
							{:else}
								{msg.content}
							{/if}
						</div>
					</div>
				{/if}

				{#if msg.role === 'assistant'}
					{#each msg.tool_calls ?? [] as toolCall, ti}
						{@const code = codeForToolCall(toolCall)}
						{#if code}
							{@const key = `hist-${i}-${ti}`}
							<div class="code-snippet">
								<div class="code-snippet-header">
									<span class="code-snippet-label">{formatToolName(toolCall.name)}</span>
									<div class="code-snippet-actions">
										<button class="code-action-btn" onclick={() => copyCode(code)}>Copy</button>
										<button class="code-action-btn" onclick={() => toggleCode(key)}>
											{shownCode.has(key) ? 'Hide code' : 'Show code'}
										</button>
									</div>
								</div>
								{#if shownCode.has(key)}
									<pre class="code-block"><code>{code}</code></pre>
								{/if}
							</div>
						{/if}
						{#each toolCall.artifacts ?? [] as artifact}
							<button class="artifact-chip" onclick={() => openArtifactInGallery(artifact.id)}>
								&#128444; {artifact.label ?? 'Figure'} &rarr;
							</button>
						{/each}
					{/each}
				{/if}
			{/each}

			{#if pendingApproval && !sending}
				<div class="approval-card">
					<p class="approval-title">Ready to run benchmark</p>
					<p class="approval-subtitle">
						{pendingApproval.config.model_names?.join(', ') ?? 'Selected models'} ·
						{pendingApproval.config.region_name ?? 'Selected region'} · Days 1–{pendingApproval
							.config.forecast_window_days ?? 30}
					</p>
					<div class="approval-actions">
						<button class="approval-run" onclick={approveSubmit}>Run benchmark</button>
						<button class="approval-cancel" onclick={declineSubmit}>Not yet</button>
					</div>
				</div>
			{/if}

			{#if sending}
				{@const runningTool = streamingTurn?.tool_calls?.find((tc) => tc.status === 'running')}
				{#if !streamingTurn || runningTool}
					<div class="thinking">
						{#if runningTool}
							<span class="thinking-label">{formatToolName(runningTool.name)}…</span>
						{/if}
						<span class="dot"></span><span class="dot"></span><span class="dot"></span>
					</div>
				{/if}
			{/if}
		</div>
	{:else}
		<section class="artifact-gallery artifact-gallery-tab">
			{#if galleryFigures.length > 0}
				<div class="artifact-gallery-header">
					<span class="artifact-gallery-title">Artifacts</span>
					<span class="artifact-gallery-count">{galleryFigures.length}</span>
				</div>
				<div class="artifact-gallery-grid">
					{#each galleryFigures as item, fi (item.artifactId)}
						{@const codeKey = artifactCodeKey(item.artifactId)}
						<div class="artifact-item">
							<FigureCard
								figure={item.figure}
								onclick={() => {
									selectedFigureIndex = fi;
								}}
							/>
							<div class="artifact-meta">
								<div class="artifact-meta-row">
									<span class="artifact-source">
										{item.toolName ? formatToolName(item.toolName) : 'generated artifact'}
									</span>
									<span class="artifact-time"
										>{new Date(item.createdAt).toLocaleTimeString([], {
											hour: 'numeric',
											minute: '2-digit'
										})}</span
									>
								</div>
								{#if item.code}
									<div class="artifact-actions">
										<button class="code-action-btn" onclick={() => copyCode(item.code!)}
											>Copy code</button
										>
										<button class="code-action-btn" onclick={() => toggleCode(codeKey)}>
											{shownCode.has(codeKey) ? 'Hide code' : 'Show code'}
										</button>
									</div>
									{#if shownCode.has(codeKey)}
										<pre class="code-block artifact-code"><code>{item.code}</code></pre>
									{/if}
								{/if}
							</div>
						</div>
					{/each}
				</div>
			{:else}
				<div class="artifact-gallery-empty">Generated figures will appear here.</div>
			{/if}
		</section>
	{/if}

	{#if error}
		<div class="chat-error">{error}</div>
	{/if}

	{#if activeTab === 'chat'}
		<div class="input-row">
			<textarea
				bind:value={input}
				onkeydown={handleKeydown}
				{placeholder}
				rows={2}
				disabled={sending}
			></textarea>
			<button
				class="send-btn"
				onclick={() => submit()}
				disabled={sending || !input.trim() || loadingSession}
			>
				{sending ? '…' : 'Send'}
			</button>
		</div>
	{/if}
</div>

{#if selectedFigureIndex !== null && galleryFigures[selectedFigureIndex]}
	<FigureLightbox
		figures={galleryFigures.map((item) => item.figure)}
		index={selectedFigureIndex}
		onclose={() => {
			selectedFigureIndex = null;
		}}
	/>
{/if}

<style>
	.chat-panel {
		display: flex;
		flex-direction: column;
		border: 1px solid var(--color-border);
		border-radius: 8px;
		overflow: hidden;
		background: var(--color-surface-raised);
		flex: 1;
		min-height: 0;
		box-shadow: -4px 0 24px rgba(0, 0, 0, 0.12);
	}

	.chat-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.55rem 0.85rem;
		border-bottom: 1px solid var(--color-border);
		background: linear-gradient(90deg, rgba(212, 147, 63, 0.06) 0%, var(--color-surface) 40%);
		flex-shrink: 0;
		position: relative;
	}

	.panel-tabs {
		display: flex;
		gap: 0.35rem;
		padding: 0.6rem 0.85rem;
		border-bottom: 1px solid var(--color-border);
		background: var(--color-surface);
		flex-shrink: 0;
	}

	.panel-tab {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.38rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 999px;
		background: transparent;
		color: var(--color-text-muted);
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		cursor: pointer;
		transition:
			background-color 0.12s,
			color 0.12s,
			border-color 0.12s;
	}

	.panel-tab:hover {
		color: var(--color-text);
		border-color: var(--color-accent-border);
	}

	.panel-tab.active {
		color: var(--color-accent);
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}

	.panel-tab-count {
		min-width: 1.15rem;
		padding: 0.05rem 0.25rem;
		border-radius: 999px;
		background: var(--color-surface-raised);
		color: inherit;
		font-size: 0.68rem;
		text-align: center;
	}

	.ai-badge {
		font-size: 0.6rem;
		font-weight: 700;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--color-accent);
		background: var(--color-accent-light);
		border: 1px solid var(--color-accent-border);
		border-radius: 3px;
		padding: 0.15rem 0.4rem;
		flex-shrink: 0;
	}

	.header-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-shrink: 0;
	}

	.copy-btn {
		padding: 0.25rem 0.6rem;
		background: none;
		border: 1px solid var(--color-border);
		border-radius: 4px;
		font-family: inherit;
		font-size: 0.72rem;
		font-weight: 500;
		color: var(--color-text-muted);
		cursor: pointer;
		transition:
			color 0.12s,
			border-color 0.12s,
			background-color 0.12s;
		white-space: nowrap;
	}
	.copy-btn:not(:disabled):hover {
		color: var(--color-accent);
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}
	.copy-btn:disabled {
		opacity: 0.35;
		cursor: default;
	}

	/* Session selector */
	.session-selector {
		position: relative;
		flex: 1;
		min-width: 0;
	}

	.session-current {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		background: none;
		border: 1px solid var(--color-border);
		border-radius: 5px;
		padding: 0.3rem 0.6rem;
		cursor: pointer;
		color: var(--color-text);
		font-family: inherit;
		font-size: 0.82rem;
		font-weight: 600;
		max-width: 100%;
		transition:
			border-color 0.15s,
			background-color 0.15s;
	}
	.session-current:hover {
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}

	.session-rename-bar {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		width: 100%;
	}

	.session-rename-input {
		flex: 1;
		min-width: 0;
		padding: 0.4rem 0.6rem;
		border: 1px solid var(--color-accent-border);
		border-radius: 5px;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: 0.82rem;
	}

	.session-rename-input:focus {
		outline: none;
		border-color: var(--color-accent);
	}

	.session-action-btn {
		padding: 0.35rem 0.65rem;
		border: 1px solid var(--color-accent-border);
		border-radius: 5px;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font: inherit;
		font-size: 0.76rem;
		font-weight: 600;
		cursor: pointer;
		transition:
			border-color 0.12s,
			background-color 0.12s,
			color 0.12s;
		white-space: nowrap;
	}

	.session-action-btn.secondary {
		border-color: var(--color-border);
		background: transparent;
		color: var(--color-text-muted);
	}

	.session-action-btn:not(:disabled):hover {
		border-color: var(--color-accent);
		background: var(--color-accent-glow);
	}

	.session-action-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.session-label {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		text-align: left;
		color: var(--color-accent);
	}

	.session-chevron {
		font-size: 0.7rem;
		color: var(--color-text-muted);
		transition: transform 0.15s;
		flex-shrink: 0;
	}
	.session-chevron.open {
		transform: rotate(180deg);
	}

	.session-dropdown {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		min-width: 240px;
		max-width: 340px;
		background: var(--color-surface-raised);
		border: 1px solid var(--color-border);
		border-radius: 7px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
		z-index: 100;
		overflow: hidden;
		padding: 0.4rem;
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
	}

	.session-new-btn {
		width: 100%;
		text-align: left;
		padding: 0.45rem 0.65rem;
		background: var(--color-accent);
		color: var(--color-bg);
		border: none;
		border-radius: 5px;
		font-family: inherit;
		font-size: 0.78rem;
		font-weight: 600;
		cursor: pointer;
		transition: opacity 0.12s;
	}
	.session-new-btn:hover {
		opacity: 0.85;
	}

	.session-divider {
		height: 1px;
		background: var(--color-border-subtle);
		margin: 0.25rem 0;
	}

	.session-item {
		display: flex;
		align-items: stretch;
		border-radius: 5px;
		overflow: hidden;
		transition: background-color 0.1s;
	}
	.session-item:hover {
		background: var(--color-accent-glow);
	}
	.session-item.active {
		background: var(--color-accent-light);
	}

	.session-item-btn {
		flex: 1;
		text-align: left;
		padding: 0.4rem 0.6rem;
		background: none;
		border: none;
		cursor: pointer;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		min-width: 0;
	}

	.session-item-title {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--color-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.session-item.active .session-item-title {
		color: var(--color-accent);
	}

	.session-item-meta {
		font-size: 0.65rem;
		color: var(--color-text-muted);
		font-family: var(--font-mono);
	}

	.session-item-delete {
		padding: 0 0.5rem;
		background: none;
		border: none;
		color: var(--color-text-dim);
		font-size: 0.85rem;
		cursor: pointer;
		border-radius: 0 5px 5px 0;
		transition:
			color 0.12s,
			background-color 0.12s;
		flex-shrink: 0;
	}
	.session-item-delete:hover {
		color: var(--color-danger);
		background: var(--color-danger-bg);
	}

	.session-item-rename {
		padding: 0 0.5rem;
		background: none;
		border: none;
		color: var(--color-text-muted);
		font-size: 0.7rem;
		font-weight: 600;
		cursor: pointer;
		transition:
			color 0.12s,
			background-color 0.12s;
		flex-shrink: 0;
	}

	.session-item-rename:hover {
		color: var(--color-accent);
		background: var(--color-accent-light);
	}

	/* Messages */
	.messages {
		flex: 1;
		overflow-y: auto;
		padding: 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.loading-msgs {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		color: var(--color-text-muted);
		font-size: 0.8rem;
		padding: 0.5rem 0;
	}

	.spinner-sm {
		width: 0.85rem;
		height: 0.85rem;
		border: 1.5px solid var(--color-border-subtle);
		border-top-color: var(--color-accent);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
		flex-shrink: 0;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.empty-chat {
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
		color: var(--color-text-muted);
		font-size: 0.875rem;
		padding: 0.5rem 0;
	}

	.artifact-gallery {
		border: 1px solid var(--color-border);
		border-radius: 0.6rem;
		background: linear-gradient(180deg, var(--color-surface) 0%, var(--color-surface-raised) 100%);
		overflow: hidden;
	}

	.artifact-gallery-tab {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-height: 0;
		margin: 0.85rem;
	}

	.artifact-gallery-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.7rem 0.9rem;
		border-bottom: 1px solid var(--color-border-subtle);
		background: rgba(212, 147, 63, 0.05);
	}

	.artifact-gallery-title {
		font-size: 0.74rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--color-accent);
	}

	.artifact-gallery-count {
		min-width: 1.5rem;
		padding: 0.1rem 0.45rem;
		border-radius: 999px;
		background: var(--color-surface);
		border: 1px solid var(--color-border-subtle);
		color: var(--color-text-muted);
		font-size: 0.72rem;
		text-align: center;
	}

	.artifact-gallery-grid {
		display: grid;
		gap: 0.75rem;
		padding: 0.85rem;
		overflow-y: auto;
	}

	.artifact-item {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
	}

	.artifact-meta {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		padding: 0 0.1rem;
	}

	.artifact-meta-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.artifact-source {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--color-accent);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.artifact-time {
		font-size: 0.72rem;
		color: var(--color-text-dim);
	}

	.artifact-actions {
		display: flex;
		gap: 0.35rem;
	}

	.artifact-code {
		font-size: 0.74rem;
	}

	.artifact-gallery-empty {
		padding: 1rem;
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}

	.suggestions {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.suggestion {
		text-align: left;
		background: transparent;
		border: 1px solid var(--color-border-subtle);
		border-left: 2px solid var(--color-accent-border);
		border-radius: 5px;
		padding: 0.5rem 0.75rem;
		font-size: 0.8rem;
		cursor: pointer;
		color: var(--color-text-muted);
		transition:
			border-color 0.15s,
			color 0.15s,
			background 0.15s;
		line-height: 1.4;
	}
	.suggestion:hover {
		background: var(--color-accent-glow);
		border-color: var(--color-accent-border);
		border-left-color: var(--color-accent);
		color: var(--color-text);
	}

	.message {
		max-width: 92%;
		font-size: 0.875rem;
		line-height: 1.6;
	}

	.message.user {
		align-self: flex-end;
	}
	.message.assistant {
		align-self: flex-start;
		width: 100%;
		max-width: 100%;
	}

	.message-content {
		padding: 0.6rem 0.875rem;
		border-radius: 8px;
		word-break: break-word;
	}

	.message.user .message-content {
		background: var(--color-accent);
		color: var(--color-bg);
		border-bottom-right-radius: 2px;
		white-space: pre-wrap;
	}

	.message.assistant .message-content {
		background: var(--color-surface);
		color: var(--color-text);
		border-bottom-left-radius: 2px;
	}

	/* Markdown prose styles */
	.prose :global(p) {
		margin: 0 0 0.6em;
	}
	.prose :global(p:last-child) {
		margin-bottom: 0;
	}
	.prose :global(strong) {
		font-weight: 600;
	}
	.prose :global(em) {
		font-style: italic;
	}
	.prose :global(ul),
	.prose :global(ol) {
		margin: 0.4em 0 0.6em 1.25em;
		padding: 0;
	}
	.prose :global(li) {
		margin-bottom: 0.2em;
	}
	.prose :global(h1),
	.prose :global(h2),
	.prose :global(h3) {
		font-weight: 600;
		margin: 0.75em 0 0.3em;
		line-height: 1.3;
	}
	.prose :global(h1) {
		font-size: 1.1em;
	}
	.prose :global(h2) {
		font-size: 1em;
	}
	.prose :global(h3) {
		font-size: 0.95em;
	}
	.prose :global(code) {
		font-family: var(--font-mono, monospace);
		font-size: 0.85em;
		background: var(--color-surface-raised);
		padding: 0.15em 0.35em;
		border-radius: 3px;
	}
	.prose :global(pre) {
		background: var(--color-surface-raised);
		border: 1px solid var(--color-border);
		border-radius: 6px;
		padding: 0.75em 1em;
		overflow-x: auto;
		margin: 0.5em 0;
	}
	.prose :global(pre code) {
		background: none;
		padding: 0;
		font-size: 0.82em;
	}
	.prose :global(blockquote) {
		border-left: 3px solid var(--color-accent);
		margin: 0.5em 0;
		padding-left: 0.75em;
		color: var(--color-text-muted);
	}
	.prose :global(table) {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85em;
		margin: 0.5em 0;
	}
	.prose :global(th),
	.prose :global(td) {
		padding: 0.3em 0.6em;
		border: 1px solid var(--color-border);
		text-align: left;
	}
	.prose :global(th) {
		font-weight: 600;
		background: var(--color-surface-raised);
	}

	.cursor {
		animation: blink 1s step-end infinite;
	}
	@keyframes blink {
		50% {
			opacity: 0;
		}
	}

	.code-snippet {
		border: 1px solid var(--color-border);
		border-radius: 6px;
		font-size: 0.78rem;
	}

	.artifact-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.25rem 0.65rem;
		border: 1px solid var(--color-accent);
		border-radius: 2rem;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 500;
		cursor: pointer;
		transition:
			background-color 0.12s,
			color 0.12s;
		margin-top: 0.25rem;
	}

	.artifact-chip:hover {
		background: var(--color-accent);
		color: var(--color-bg);
	}

	.code-snippet-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.35rem 0.65rem;
		background: var(--color-surface);
		border-radius: 6px;
		gap: 0.5rem;
	}

	.code-snippet-label {
		font-size: 0.7rem;
		font-weight: 600;
		color: var(--color-accent);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.code-snippet-actions {
		display: flex;
		gap: 0.35rem;
	}

	.code-action-btn {
		padding: 0.15rem 0.5rem;
		background: none;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		font-family: inherit;
		font-size: 0.68rem;
		color: var(--color-text-muted);
		cursor: pointer;
		transition:
			color 0.12s,
			border-color 0.12s;
	}
	.code-action-btn:hover {
		color: var(--color-accent);
		border-color: var(--color-accent);
	}

	.code-block {
		margin: 0;
		padding: 0.75rem 1rem;
		background: var(--color-bg);
		border-top: 1px solid var(--color-border);
		overflow-x: auto;
		font-family: var(--font-mono, monospace);
		font-size: 0.78rem;
		line-height: 1.5;
		color: var(--color-text);
		white-space: pre;
	}

	.tool-indicator {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
		color: var(--color-text-muted);
		padding: 0.2rem 0;
	}
	.tool-indicator.done {
		opacity: 0.5;
	}
	.tool-icon {
		font-size: 0.7rem;
	}

	.approval-card {
		border: 1px solid var(--color-accent-border);
		border-radius: 0.5rem;
		background: var(--color-accent-light);
		padding: 0.85rem 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.approval-title {
		margin: 0;
		font-size: 0.88rem;
		font-weight: 700;
		color: var(--color-accent);
	}
	.approval-subtitle {
		margin: 0;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}
	.approval-actions {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.25rem;
	}
	.approval-run {
		border: 0;
		border-radius: 0.35rem;
		background: var(--color-accent);
		color: white;
		padding: 0.45rem 0.9rem;
		font: inherit;
		font-size: 0.82rem;
		font-weight: 700;
		cursor: pointer;
	}
	.approval-cancel {
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		padding: 0.45rem 0.9rem;
		font: inherit;
		font-size: 0.82rem;
		font-weight: 700;
		cursor: pointer;
	}
	.approval-cancel:hover {
		color: var(--color-text);
	}

	.thinking {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 0.4rem 0;
	}
	.thinking-label {
		font-size: 0.72rem;
		color: var(--color-text-muted);
		font-style: italic;
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--color-text-muted);
		animation: bounce 1.2s infinite;
	}
	.dot:nth-child(2) {
		animation-delay: 0.2s;
	}
	.dot:nth-child(3) {
		animation-delay: 0.4s;
	}
	@keyframes bounce {
		0%,
		80%,
		100% {
			transform: translateY(0);
		}
		40% {
			transform: translateY(-5px);
		}
	}

	.chat-error {
		padding: 0.5rem 1rem;
		background: #fef2f2;
		color: #b91c1c;
		font-size: 0.8rem;
		border-top: 1px solid #fecaca;
	}

	.input-row {
		display: flex;
		gap: 0.5rem;
		padding: 0.75rem;
		border-top: 1px solid var(--color-border);
		flex-shrink: 0;
	}

	textarea {
		flex: 1;
		resize: none;
		padding: 0.5rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 6px;
		font-size: 0.875rem;
		font-family: inherit;
		background: var(--color-surface);
		color: var(--color-text);
		line-height: 1.4;
	}
	textarea:focus {
		outline: none;
		border-color: var(--color-accent);
	}
	textarea:disabled {
		opacity: 0.6;
	}

	.send-btn {
		padding: 0 1rem;
		background: var(--color-accent);
		color: var(--color-bg);
		border: none;
		border-radius: 6px;
		font-size: 0.875rem;
		font-weight: 500;
		cursor: pointer;
		transition: opacity 0.15s;
		align-self: flex-end;
		height: 2.25rem;
	}
	.send-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.send-btn:not(:disabled):hover {
		opacity: 0.85;
	}
</style>
