<script lang="ts">
	import FigureLightbox from '$lib/components/FigureLightbox.svelte';
	import ChatHeader from '$lib/components/ChatHeader.svelte';
	import ChatMessages from '$lib/components/ChatMessages.svelte';
	import ChatArtifactGallery from '$lib/components/ChatArtifactGallery.svelte';
	import { ChatSessionState } from '$lib/chat/session.svelte';
	import { sessionFigures } from '$lib/chat/format';
	import type {
		BenchmarkRunSpec,
		BenchmarkValidation,
		Blend,
		BlendRunSpec,
		BlendValidation,
		ChatScope,
		Job
	} from '$lib/api';

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
		onBlendConfig?: (config: BlendRunSpec, validation?: BlendValidation | null) => void;
		onBlendSubmitted?: (runId: string, jobs: Blend[], sessionId: string | null) => void;
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
		onBenchmarkSubmitted,
		onBlendConfig,
		onBlendSubmitted
	}: Props = $props();

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
		const region =
			firstJob?.region_name ??
			titleCase(firstJob?.region_id ?? firstJob?.params?.region ?? scope.key);
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

	const chat = new ChatSessionState(sessionScope, {
		onSessionReady: (id) => onSessionReady?.(id),
		onJobsCreated: (created) => onJobsCreated?.(created),
		onBenchmarkConfig: (config, validation) => onBenchmarkConfig?.(config, validation),
		onBenchmarkSubmitted: (runId, created, sessionId) =>
			onBenchmarkSubmitted?.(runId, created, sessionId),
		onBlendConfig: (config, validation) => onBlendConfig?.(config, validation),
		onBlendSubmitted: (runId, created, sessionId) => onBlendSubmitted?.(runId, created, sessionId)
	});

	let input = $state('');
	let initialMessageHandled = $state(false);
	let handledExternalPromptNonce = $state(0);
	let activeTab = $state<'chat' | 'artifacts'>('chat');
	let selectedFigureIndex = $state<number | null>(null);

	const galleryFigures = $derived(sessionFigures(chat.visibleTurns));

	$effect(() => {
		if (!showArtifacts && activeTab === 'artifacts') activeTab = 'chat';
	});

	$effect(() => {
		void scopeKey;
		void scopeKind;
		void jobs;
		chat.syncScope(preferredSessionId);
	});

	$effect(() => {
		if (!chat.sessionId || chat.sending || initialMessageHandled || !initialMessage.trim()) return;
		initialMessageHandled = true;
		void chat.submit(initialMessage.trim());
	});

	$effect(() => {
		if (
			!chat.sessionId ||
			chat.sending ||
			!externalPrompt?.trim() ||
			externalPromptNonce === handledExternalPromptNonce
		) {
			return;
		}
		handledExternalPromptNonce = externalPromptNonce;
		void chat.submit(externalPrompt.trim());
	});

	function send(overrideText?: string) {
		const text = (overrideText ?? input).trim();
		if (!text || !chat.canSend) return;
		if (!overrideText) input = '';
		void chat.submit(text);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}

	function openArtifactInGallery(artifactId: string) {
		const idx = galleryFigures.findIndex((f) => f.artifactId === artifactId);
		if (idx !== -1) selectedFigureIndex = idx;
	}
</script>

<div class="chat-panel">
	<ChatHeader {chat} />

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
		<ChatMessages
			{chat}
			{emptyMessage}
			{suggestions}
			onSuggestion={(text) => send(text)}
			onOpenArtifact={openArtifactInGallery}
		/>
	{:else}
		<ChatArtifactGallery
			figures={galleryFigures}
			onOpenFigure={(index) => {
				selectedFigureIndex = index;
			}}
		/>
	{/if}

	{#if chat.error}
		<div class="chat-error">{chat.error}</div>
	{/if}

	{#if activeTab === 'chat'}
		<div class="input-row">
			<textarea
				bind:value={input}
				onkeydown={handleKeydown}
				{placeholder}
				rows={2}
				disabled={chat.sending}></textarea>
			<button
				class="send-btn"
				onclick={() => send()}
				disabled={chat.sending || !input.trim() || chat.loadingSession}
			>
				{chat.sending ? '…' : 'Send'}
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
