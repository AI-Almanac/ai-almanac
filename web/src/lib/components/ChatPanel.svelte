<script lang="ts">
	import { onMount } from 'svelte';
	import FigureLightbox from '$lib/components/FigureLightbox.svelte';
	import ChatHeader from '$lib/components/ChatHeader.svelte';
	import ChatMessages from '$lib/components/ChatMessages.svelte';
	import ChatArtifactGallery from '$lib/components/ChatArtifactGallery.svelte';
	import ChatCompare from '$lib/components/ChatCompare.svelte';
	import QueuedFollowUps from '$lib/components/QueuedFollowUps.svelte';
	import { isQueued, sessionGainedTurn, settledIds } from '$lib/chat/follow-ups';
	import { BETA_NOTICE, betaNoticeDue, betaNoticeKey, foldsToolbar } from '$lib/chat/column';
	import { ChatSessionState } from '$lib/chat/session.svelte';
	import { ComparisonState, compareBlocker } from '$lib/chat/compare.svelte';
	import { sessionFigures } from '$lib/chat/format';
	import { blindCompare, getRulesetOptions } from '$lib/api';
	import { pollWhileActive } from '$lib/poll';
	import type {
		BenchmarkRunSpec,
		BenchmarkValidation,
		Blend,
		BlendRunSpec,
		BlendValidation,
		ChatScope,
		Job,
		RulesetOption,
		ChatArtifact,
		ChatMessage,
		FollowUp
	} from '$lib/api';
	import type {
		ActionSource,
		InterfaceAction,
		WorkspaceTarget
	} from '$lib/interface-actions/actions';

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
		onSessionReady?: (sessionId: string) => void;
		onJobsCreated?: (jobs: Job[]) => void;
		onBenchmarkConfig?: (config: BenchmarkRunSpec, validation?: BenchmarkValidation | null) => void;
		onBenchmarkSubmitted?: (runId: string, jobs: Job[], sessionId: string | null) => void;
		onBlendConfig?: (config: BlendRunSpec, validation?: BlendValidation | null) => void;
		onBlendSubmitted?: (runId: string, jobs: Blend[], sessionId: string | null) => void;
		/** Lets the page widen this column: two answers need more than the
		 * assistant rail's normal width to be readable side by side. */
		onComparingChange?: (comparing: boolean) => void;
		/** The settled transcript, for hosts that label results by the question behind them. */
		onTranscriptChange?: (messages: ChatMessage[]) => void;
		/** Where chat links and figures open; when set, the host owns artifacts and the Artifacts tab is hidden. */
		onOpenItem?: (target: WorkspaceTarget) => void;
		/** What the host does when the assistant, or a button in the conversation, asks the interface to act. */
		onInterfaceAction?: (action: InterfaceAction, source: ActionSource) => void;
		/** Current names of the workspace's files by id, so references show what the user calls them. */
		fileTitles?: Record<string, string>;
		/** Latest known status per job id, for runs the assistant submitted. */
		jobStatuses?: Record<string, string>;
		/** A turn to scroll to and briefly highlight. */
		focusTurnId?: string | null;
		/** Work the assistant queued in this workspace until its jobs finish. */
		followUps?: FollowUp[];
		onCancelFollowUp?: (followUpId: string) => void;
		/** Told when the assistant starts and stops working, so a collapsed host can show it. */
		onWorkingChange?: (working: boolean) => void;
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
		onSessionReady,
		onJobsCreated,
		onBenchmarkConfig,
		onBenchmarkSubmitted,
		onBlendConfig,
		onBlendSubmitted,
		onComparingChange,
		onTranscriptChange,
		onOpenItem,
		onInterfaceAction,
		fileTitles,
		jobStatuses,
		focusTurnId = null,
		followUps = [],
		onCancelFollowUp,
		onWorkingChange
	}: Props = $props();

	let composer = $state<HTMLTextAreaElement | null>(null);

	/** For a host that opens the conversation and wants typing to start there. */
	export function focusComposer() {
		composer?.focus();
	}

	const toolbarFolded = $derived(foldsToolbar(scopeKind));

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
		onInterfaceAction: (action) => onInterfaceAction?.(action, 'assistant'),
		onBlendSubmitted: (runId, created, sessionId) => onBlendSubmitted?.(runId, created, sessionId)
	});

	$effect(() => onTranscriptChange?.(chat.messages));

	const queuedHere = $derived(
		followUps.filter((f) => f.session_id === chat.sessionId && isQueued(f))
	);
	// A follow-up turn runs on the server; sending alongside it would race the transcript.
	const followUpRunning = $derived(queuedHere.some((f) => f.status === 'running'));
	const inputLocked = $derived(chat.sending || followUpRunning);

	// Waiting on an approval ends the stream, so it reports as not working.
	let reportedWorking = false;
	$effect(() => {
		const working = inputLocked;
		if (working === reportedWorking) return;
		reportedWorking = working;
		onWorkingChange?.(working);
	});

	let seenSettled: Set<string> | null = null;
	$effect(() => {
		const previous = seenSettled;
		seenSettled = settledIds(followUps);
		if (
			previous &&
			chat.sessionId &&
			!chat.sending &&
			sessionGainedTurn(previous, followUps, chat.sessionId)
		)
			void chat.loadSession(chat.sessionId);
	});

	let input = $state('');
	let initialMessageHandled = $state(false);
	let handledExternalPromptNonce = $state(0);
	let activeTab = $state<'chat' | 'artifacts'>('chat');
	let selectedFigureIndex = $state<number | null>(null);

	const galleryFigures = $derived(sessionFigures(chat.visibleTurns));
	// Any chat that has produced figures gets the tab; one that has not (a setup
	// chat, a fresh session) has nothing to put in it. A host that opens items
	// itself shows figures there instead.
	const artifactsTabShown = $derived(galleryFigures.length > 0 && !onOpenItem);

	$effect(() => {
		if (!artifactsTabShown && activeTab === 'artifacts') activeTab = 'chat';
	});

	$effect(() => {
		void scopeKey;
		void scopeKind;
		void jobs;
		chat.syncScope(preferredSessionId);
	});

	// Keep asking while a run in scope is still going, so its notice appears when it
	// finishes. No reactive reads in the body: the interval is created once.
	$effect(() => pollWhileActive(() => chat.jobActivity.active > 0, chat.refreshJobActivity, 5000));

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
		if (!text || !chat.canSend || followUpRunning) return;
		if (!overrideText) input = '';
		void chat.submit(text);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}

	// ponytail: localStorage, not a user preference row — this is a one-time
	// acknowledgement, and re-showing it on a new device is the harmless failure.
	let showBetaNote = $state(false);

	function rememberBetaNoteSeen() {
		localStorage.setItem(betaNoticeKey(scopeKind), '1');
	}

	onMount(() => {
		showBetaNote = betaNoticeDue(localStorage.getItem(betaNoticeKey(scopeKind)));
		// Where the toolbar folds away the notice is read once, then lives on in the badge's tooltip.
		if (showBetaNote && foldsToolbar(scopeKind)) rememberBetaNoteSeen();
	});

	function dismissBetaNote() {
		showBetaNote = false;
		rememberBetaNoteSeen();
	}

	function openArtifact(artifact: ChatArtifact) {
		if (onOpenItem) {
			onOpenItem({ kind: 'artifact', ref: artifact.workspace_artifact_id ?? artifact.id });
			return;
		}
		const idx = galleryFigures.findIndex((f) => f.artifactId === artifact.id);
		if (idx !== -1) selectedFigureIndex = idx;
	}

	// ---- Assistant styles and blind A/B comparison -------------------------

	let rulesetOptions = $state<RulesetOption[]>([]);
	let comparisonsEnabled = $state(false);
	let compareAvailable = $state(false);
	const comparison = new ComparisonState();
	let comparing = $state(false);

	// ponytail: localStorage, like the beta note — an opt-in view preference,
	// and re-toggling it on a new device is the harmless failure.
	const COMPARISON_MODE_KEY = 'almanac.comparisonModeEnabled';
	let comparisonMode = $state(false);
	// A stored preference cannot outlive the feature: if an operator switches
	// comparisons off, the mode it enabled goes with them.
	const comparisonModeActive = $derived(comparisonMode && comparisonsEnabled);

	function toggleComparisonMode() {
		comparisonMode = !comparisonMode;
		localStorage.setItem(COMPARISON_MODE_KEY, comparisonMode ? '1' : '0');
	}

	// The pair a comparison runs; defaults to the active ruleset vs the first
	// other exposed one. The user changes it from the ⋯ menu.
	let compareArmIds = $state<[string, string]>(['', '']);

	onMount(() => {
		comparisonMode = localStorage.getItem(COMPARISON_MODE_KEY) === '1';
		getRulesetOptions()
			.then((options) => {
				rulesetOptions = options.rulesets;
				comparisonsEnabled = options.comparisons_enabled;
				compareAvailable = options.compare_available;
				const first = options.rulesets.find((r) => r.is_active) ?? options.rulesets[0];
				const second = options.rulesets.find((r) => r.id !== first?.id);
				compareArmIds = [first?.id ?? '', second?.id ?? ''];
			})
			.catch(() => undefined);
	});

	const compareReady = $derived(
		compareAvailable &&
			compareArmIds[0] !== '' &&
			compareArmIds[1] !== '' &&
			compareArmIds[0] !== compareArmIds[1]
	);

	const lastQuestion = $derived(
		[...chat.messages].reverse().find((message) => message.role === 'user')?.content ?? ''
	);
	// With nothing typed, A/B re-asks the last question. Requiring fresh text
	// meant you could not compare the answer actually in front of you — the one
	// case where wanting a second opinion is most obvious.
	const compareText = $derived(input.trim() || lastQuestion);

	const blocker = $derived(compareBlocker(compareReady, compareText));

	function startCompare() {
		const text = compareText;
		if (!text || comparison.running || !compareReady) return;
		input = '';
		comparing = true;
		onComparingChange?.(true);
		void comparison.start(
			text,
			blindCompare(text, compareArmIds, {
				sourceSessionId: chat.sessionId ?? undefined,
				scope: sessionScope()
			})
		);
	}

	async function closeCompare() {
		await comparison.discard();
		comparing = false;
		onComparingChange?.(false);
	}
</script>

{#snippet toolbar()}
	<ChatHeader
		{chat}
		folded={toolbarFolded}
		{rulesetOptions}
		comparisonMode={comparisonModeActive}
		{comparisonsEnabled}
		{compareAvailable}
		{compareArmIds}
		onToggleComparisonMode={toggleComparisonMode}
		onCompareArmChange={(index, id) => {
			compareArmIds[index] = id;
		}}
	/>
{/snippet}

<div class="chat-panel">
	{#if !toolbarFolded}
		{@render toolbar()}
	{/if}

	{#if showBetaNote}
		<div class="beta-note">
			<p>{BETA_NOTICE}</p>
			<button class="beta-note-dismiss" onclick={dismissBetaNote} aria-label="Dismiss">×</button>
		</div>
	{/if}

	{#if !onOpenItem}
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
			{#if artifactsTabShown}
				<button
					class="panel-tab"
					class:active={activeTab === 'artifacts'}
					onclick={() => {
						activeTab = 'artifacts';
					}}
				>
					Artifacts
					<span class="panel-tab-count">{galleryFigures.length}</span>
				</button>
			{/if}
		</div>
	{/if}

	{#if comparing}
		<ChatCompare {comparison} onClose={() => void closeCompare()} />
	{:else if activeTab === 'chat'}
		<ChatMessages
			{chat}
			{emptyMessage}
			{suggestions}
			canRate={comparisonModeActive}
			onSuggestion={(text) => send(text)}
			onOpenArtifact={openArtifact}
			{onOpenItem}
			{onInterfaceAction}
			{fileTitles}
			{jobStatuses}
			{focusTurnId}
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

	{#if activeTab === 'chat' && !comparing}
		{#if comparisonModeActive && compareAvailable}
			<p class="compare-hint">
				{#if blocker}
					{blocker}.
				{:else if input.trim()}
					<strong>A/B</strong> answers this in two assistant styles, side by side, for you to vote on.
				{:else}
					<strong>A/B</strong> re-asks your last question in two assistant styles, side by side — or type
					a new one.
				{/if}
			</p>
		{/if}
		{#if queuedHere.length > 0}
			<QueuedFollowUps followUps={queuedHere} onCancel={onCancelFollowUp} />
		{/if}
		<div class="input-row">
			{#if toolbarFolded}
				{@render toolbar()}
			{/if}
			<textarea
				bind:this={composer}
				bind:value={input}
				onkeydown={handleKeydown}
				{placeholder}
				rows={2}
				disabled={inputLocked}></textarea>
			{#if comparisonModeActive && compareAvailable}
				<button
					class="ab-btn"
					onclick={startCompare}
					disabled={chat.sending || chat.loadingSession || !!blocker}
					title={blocker ??
						'Answer side by side in two assistant styles and vote on the better one'}
				>
					A/B
				</button>
			{/if}
			<button
				class="send-btn"
				onclick={() => send()}
				disabled={inputLocked || !input.trim() || chat.loadingSession}
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
		padding: 0.38rem var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 999px;
		background: transparent;
		color: var(--color-text-muted);
		font-size: var(--text-size-caption);
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
		padding: 0.05rem var(--space-1);
		border-radius: 999px;
		background: var(--color-surface-raised);
		color: inherit;
		font-size: var(--text-size-caption);
		text-align: center;
	}

	.chat-error {
		padding: var(--space-2) var(--space-4);
		background: #fef2f2;
		color: #b91c1c;
		font-size: var(--text-size-label);
		border-top: 1px solid #fecaca;
	}

	.input-row {
		display: flex;
		gap: var(--space-2);
		padding: var(--space-3);
		border-top: 1px solid var(--color-border);
		flex-shrink: 0;
	}

	textarea {
		flex: 1;
		resize: none;
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 6px;
		font-size: var(--text-size-body);
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
		padding: 0 var(--space-4);
		background: var(--color-accent);
		color: var(--color-bg);
		border: none;
		border-radius: 6px;
		font-size: var(--text-size-body);
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
	.compare-hint {
		margin: 0;
		padding: 0.4rem var(--space-3) 0;
		font-size: var(--text-size-caption);
		line-height: 1.4;
		color: var(--color-text-muted);
	}
	.ab-btn {
		padding: 0 var(--space-3);
		background: var(--color-surface);
		color: var(--color-text-muted);
		border: 1px solid var(--color-border);
		border-radius: 6px;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
		align-self: flex-end;
		height: 2.25rem;
	}
	.ab-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.ab-btn:not(:disabled):hover {
		color: var(--color-accent);
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}
	.beta-note {
		display: flex;
		align-items: flex-start;
		gap: var(--space-2);
		padding: 0.6rem var(--space-3);
		background: var(--color-status-running-bg);
		border-bottom: 1px solid var(--color-status-running);
		color: var(--color-status-running);
		font-size: var(--text-size-label);
		line-height: 1.4;
	}
	.beta-note p {
		margin: 0;
		flex: 1;
	}
	.beta-note-dismiss {
		background: none;
		border: none;
		color: inherit;
		cursor: pointer;
		font-size: var(--text-size-title);
		line-height: 1;
		padding: 0 0.15rem;
		flex-shrink: 0;
	}
</style>
