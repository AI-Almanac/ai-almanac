<script lang="ts">
	import { tick } from 'svelte';
	import type { ChatSessionState } from '$lib/chat/session.svelte';
	import ChatActivity from '$lib/components/ChatActivity.svelte';
	import ChatJobNotice from '$lib/components/ChatJobNotice.svelte';
	import ChatRunConfirmation from '$lib/components/ChatRunConfirmation.svelte';
	import ChatSetupHandoff from '$lib/components/ChatSetupHandoff.svelte';
	import OpenItemCard from '$lib/components/chat/OpenItemCard.svelte';
	import SourcesLine from '$lib/components/chat/SourcesLine.svelte';
	import { resultCardFor } from '$lib/components/chat/result-cards';
	import type { StepContext } from '$lib/components/chat/step-context';
	import { setupHandoffs } from '$lib/chat/handoff';
	import { conversationItems } from '$lib/chat/conversation';
	import { followUpReplyIds, isFollowUpTurn } from '$lib/chat/follow-ups';
	import { currentGuardrails } from '$lib/chat/guardrails';
	import { linkTarget, renderMarkdown } from '$lib/chat/markdown';
	import { turnTimeline, type ActivityGroup } from '$lib/chat/timeline';
	import { figureCard } from '$lib/chat/reply-cards';
	import { rateChatTurn, type ChatArtifact, type ChatMessage, type ChatToolCall } from '$lib/api';
	import type {
		ActionSource,
		InterfaceAction,
		WorkspaceTarget
	} from '$lib/interface-actions/actions';

	interface Props {
		chat: ChatSessionState;
		emptyMessage: string;
		suggestions: string[];
		/** Comparison mode is the opt-in for all feedback UI, thumbs included. */
		canRate?: boolean;
		onSuggestion: (text: string) => void;
		onOpenArtifact: (artifact: ChatArtifact) => void;
		onOpenItem?: (target: WorkspaceTarget) => void;
		onInterfaceAction?: (action: InterfaceAction, source: ActionSource) => void;
		jobStatuses?: Record<string, string>;
		focusTurnId?: string | null;
		/** Current names of the workspace's files by id, so references show what the user calls them. */
		fileTitles?: Record<string, string>;
	}

	const {
		chat,
		emptyMessage,
		suggestions,
		canRate = false,
		onSuggestion,
		onOpenArtifact,
		onOpenItem,
		onInterfaceAction,
		jobStatuses,
		focusTurnId = null,
		fileTitles
	}: Props = $props();

	let messagesEl = $state<HTMLElement | null>(null);

	const conversation = $derived(
		conversationItems(chat.visibleTurns, chat.jobActivity.events, !chat.sending)
	);
	const liveGuardrails = $derived(currentGuardrails(chat.visibleTurns));
	const resultContext: StepContext = {
		get jobStatuses() {
			return jobStatuses;
		},
		get onOpenItem() {
			return onOpenItem;
		},
		get onInterfaceAction() {
			return onInterfaceAction;
		}
	};
	let ratings = $state<Record<string, 1 | -1>>({});
	let highlightedTurnId = $state<string | null>(null);
	let scrolledToTurnId: string | null = null;

	async function rate(turnId: string, value: 1 | -1) {
		const sessionId = chat.sessionId;
		if (!sessionId || ratings[turnId] === value) return;
		ratings = { ...ratings, [turnId]: value };
		try {
			await rateChatTurn(sessionId, turnId, value);
		} catch {
			// A rating is telemetry, not the user's work — drop the optimistic
			// mark rather than interrupting the conversation with an error.
			const { [turnId]: _dropped, ...rest } = ratings;
			ratings = rest;
		}
	}

	/** Finished calls whose result is worth a card of its own below the group. */
	function resultCalls(group: ActivityGroup): ChatToolCall[] {
		return group.steps.flatMap((step) =>
			step.kind === 'tool' &&
			step.toolCall.status === 'completed' &&
			resultCardFor(step.toolCall.name)
				? [step.toolCall]
				: []
		);
	}

	// A host that opens items itself takes wide content out of the conversation:
	// figures and sources become cards, and tables scroll in a box of their own.
	const hosted = $derived(!!onOpenItem);

	function replyHtml(text: string): string {
		return renderMarkdown(text, { openArtifactLinks: hosted, scrollTables: hosted });
	}

	function openFigure(group: ActivityGroup, artifactId: string) {
		const artifact = group.figures.find((figure) => figure.id === artifactId);
		if (artifact) onOpenArtifact(artifact);
	}

	// The user did not write what started these, so "send it again" would not help.
	const followUpReplies = $derived(followUpReplyIds(chat.messages));

	function isSilentTurn(msg: ChatMessage): boolean {
		return msg.id !== chat.streamingTurn?.id && !msg.content.trim() && !msg.tool_calls?.length;
	}

	function openArtifactLink(event: MouseEvent) {
		const target = linkTarget(event.target);
		if (target) onOpenItem?.(target);
	}

	// Artifact links arrive inside rendered markdown, so one delegated listener
	// serves them all without the HTML carrying any handler of its own.
	$effect(() => {
		const el = messagesEl;
		if (!el) return;
		el.addEventListener('click', openArtifactLink);
		return () => el.removeEventListener('click', openArtifactLink);
	});

	// Follow new output only while the reader is at the bottom, so scrolling up to
	// reread history is not yanked back by every streamed token.
	let followingLatest = true;

	function trackFollowing() {
		if (!messagesEl) return;
		const { scrollTop, scrollHeight, clientHeight } = messagesEl;
		followingLatest = scrollHeight - scrollTop - clientHeight < 48;
	}

	$effect(() => {
		if (chat.sending) followingLatest = true;
	});

	$effect(() => {
		void chat.messages;
		void chat.streamingTurn;
		void chat.jobActivity;
		// The approval card appears once the stream ends, below everything else.
		void chat.pendingApproval;
		void chat.sending;
		tick().then(() => {
			if (messagesEl && followingLatest) messagesEl.scrollTop = messagesEl.scrollHeight;
		});
	});

	// Registered after the bottom scroll so a requested turn wins when both run.
	$effect(() => {
		void chat.messages;
		const turnId = focusTurnId;
		// The host clears the request between two for the same turn, so the second scrolls too.
		if (!turnId) {
			scrolledToTurnId = null;
			return;
		}
		if (turnId === scrolledToTurnId) return;
		tick().then(() => {
			const el = messagesEl?.querySelector(`[data-turn-id="${CSS.escape(turnId)}"]`);
			if (!el) return;
			scrolledToTurnId = turnId;
			el.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
			highlightedTurnId = turnId;
			setTimeout(() => {
				if (highlightedTurnId === turnId) highlightedTurnId = null;
			}, 2000);
		});
	});
</script>

<div class="messages" bind:this={messagesEl} onscroll={trackFollowing}>
	{#if chat.loadingSession}
		<div class="loading-msgs">
			<div class="spinner-sm"></div>
			<span>Loading…</span>
		</div>
	{:else if chat.messages.length === 0 && !chat.sending}
		<div class="empty-chat">
			<p>{emptyMessage}</p>
			<div class="suggestions">
				{#each suggestions as suggestion}
					<button class="suggestion" onclick={() => onSuggestion(suggestion)}>
						{suggestion}
					</button>
				{/each}
			</div>
		</div>
	{/if}

	{#each conversation as entry (entry.key)}
		{#if entry.kind === 'job_event'}
			<ChatJobNotice event={entry.event} followUp={entry.followUp} onFollowUp={onSuggestion} />
		{:else}
			{@const msg = entry.turn}
			<div
				class="turn"
				class:highlighted={highlightedTurnId === msg.id}
				id="chat-turn-{msg.id}"
				data-turn-id={msg.id}
			>
				{#if msg.role === 'user' && isFollowUpTurn(msg)}
					<p class="system-note">Picked up after your runs finished</p>
				{:else if msg.role === 'user'}
					<div class="message user">
						<div class="message-content">{msg.content}</div>
					</div>
				{:else}
					{@const timeline = turnTimeline(msg)}
					{@const streaming = msg.id === chat.streamingTurn?.id}
					{#each timeline as item, ii (item.key)}
						{#if item.kind === 'text'}
							<div class="message assistant">
								<div class="message-content prose">
									{@html replyHtml(item.text)}
								</div>
							</div>
						{:else}
							<ChatActivity
								group={item}
								drawFigures={!hosted}
								showTechnicalDetails={hosted}
								live={streaming && ii === timeline.length - 1}
								onOpenArtifact={(artifactId) => openFigure(item, artifactId)}
							/>
							{#if hosted}
								{#each item.figures as artifact (artifact.id)}
									<OpenItemCard
										card={figureCard(artifact, fileTitles)}
										onOpen={() => onOpenArtifact(artifact)}
									/>
								{/each}
							{/if}
							{#each resultCalls(item) as call (call.id)}
								{@const ResultCard = resultCardFor(call.name)}
								<ResultCard {call} context={resultContext} />
							{/each}
						{/if}
					{/each}

					{#if msg.status === 'failed' && followUpReplies.has(msg.id)}
						<p class="system-note">
							The assistant couldn't finish its report on your runs. Ask it to carry on when you're
							ready.
						</p>
					{:else if msg.status === 'failed'}
						<p class="system-note">This reply didn't finish. Send your message again to retry.</p>
					{:else if isSilentTurn(msg)}
						<p class="system-note">The assistant ended its turn without replying.</p>
					{/if}

					{#each setupHandoffs(msg) as handoff (handoff.href)}
						<ChatSetupHandoff {handoff} />
					{/each}

					{#if onOpenItem && msg.source_artifacts?.length && !streaming}
						<SourcesLine
							sources={msg.source_artifacts}
							{fileTitles}
							onOpen={(source) => onOpenItem({ kind: 'artifact', ref: source.id })}
						/>
					{/if}

					{#if canRate && msg.content && !streaming}
						<div class="rating">
							<span class="rating-label">Was this useful?</span>
							<button
								class="rating-btn"
								class:chosen={ratings[msg.id] === 1}
								aria-label="Helpful"
								onclick={() => void rate(msg.id, 1)}>&#128077;</button
							>
							<button
								class="rating-btn"
								class:chosen={ratings[msg.id] === -1}
								aria-label="Not helpful"
								onclick={() => void rate(msg.id, -1)}>&#128078;</button
							>
						</div>
					{/if}

					<!--
					Rendered from the backend's guardrail events, not from the assistant's
					prose. The reply above may explain these well, badly, or not at all;
					the user is told either way.
				-->
					{#each liveGuardrails.get(msg.id) ?? [] as notice}
						{#if notice.errors.length}
							<div class="guardrail guardrail-error">
								<p class="guardrail-title">This configuration cannot run</p>
								<ul>
									{#each notice.errors as item (item)}
										<li>{item}</li>
									{/each}
								</ul>
							</div>
						{/if}
						{#if notice.warnings.length}
							<div class="guardrail guardrail-warning">
								<p class="guardrail-title">Read these results with care</p>
								<ul>
									{#each notice.warnings as item (item)}
										<li>{item}</li>
									{/each}
								</ul>
							</div>
						{/if}
					{/each}
				{/if}
			</div>
		{/if}
	{/each}

	{#if chat.pendingApproval && !chat.sending}
		<ChatRunConfirmation
			approval={chat.pendingApproval}
			onApprove={chat.approveSubmit}
			onDecline={chat.declineSubmit}
		/>
	{/if}

	{#if chat.sending && !chat.streamingTurn}
		<div class="thinking" aria-label="Assistant is working">
			{#if chat.assistantWaking}
				<span class="thinking-label">Waking the assistant — this can take up to a minute…</span>
			{/if}
			<span class="dot"></span><span class="dot"></span><span class="dot"></span>
		</div>
	{/if}
</div>

<style>
	.messages {
		flex: 1;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: var(--space-4);
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.loading-msgs {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
		padding: var(--space-2) 0;
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
		font-size: var(--text-size-body);
		padding: var(--space-2) 0;
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
		padding: var(--space-2) var(--space-3);
		font-size: var(--text-size-label);
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

	.turn {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		border-radius: 0.5rem;
		transition: box-shadow 0.3s;
	}
	.turn.highlighted {
		box-shadow: 0 0 0 0.15rem var(--color-accent-border);
		background: var(--color-accent-glow);
	}

	.prose :global(.artifact-link) {
		display: inline-flex;
		align-items: center;
		padding: 0 var(--space-2);
		border: 1px solid var(--color-accent);
		border-radius: 2rem;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 500;
		cursor: pointer;
	}
	.prose :global(.artifact-link:hover) {
		background: var(--color-accent);
		color: var(--color-bg);
	}

	.system-note {
		align-self: center;
		margin: 0;
		padding: 0.2rem var(--space-3);
		border-radius: 2rem;
		background: var(--color-surface-muted);
		color: var(--color-text-muted);
		font-size: var(--text-size-caption);
	}

	.message {
		max-width: 92%;
		font-size: var(--text-size-body);
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
		font-size: var(--text-size-lead);
	}
	.prose :global(h2),
	.prose :global(h3) {
		font-size: var(--text-size-body);
	}
	.prose :global(code) {
		font-family: var(--font-mono, monospace);
		font-size: var(--text-size-label);
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
		font-size: var(--text-size-caption);
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
		font-size: var(--text-size-label);
		margin: 0.5em 0;
	}
	.prose :global(.table-scroll) {
		max-width: 100%;
		overflow-x: auto;
		margin: 0.5em 0;
	}
	/* Sized by its content so a wide table scrolls instead of squeezing its columns. */
	.prose :global(.table-scroll table) {
		width: max-content;
		min-width: 100%;
		margin: 0;
	}
	.prose :global(.table-scroll th),
	.prose :global(.table-scroll td) {
		max-width: 16rem;
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

	.rating {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
	}
	.rating-btn {
		background: none;
		border: 1px solid transparent;
		border-radius: 0.3rem;
		cursor: pointer;
		padding: 0.1rem var(--space-1);
		font-size: var(--text-size-label);
		opacity: 0.55;
	}
	.rating-btn:hover,
	.rating-btn.chosen {
		opacity: 1;
		border-color: var(--color-border);
	}
	.guardrail {
		border-radius: 0.45rem;
		border: 1px solid;
		padding: 0.6rem var(--space-3);
		font-size: var(--text-size-label);
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.guardrail ul {
		margin: 0;
		padding-left: 1.1rem;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.guardrail-title {
		margin: 0;
		font-weight: 700;
	}
	.guardrail-warning {
		color: var(--color-status-running);
		background: var(--color-status-running-bg);
		border-color: var(--color-status-running);
	}
	.guardrail-error {
		color: var(--color-status-failed);
		background: var(--color-status-failed-bg);
		border-color: var(--color-status-failed);
	}

	.thinking {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		padding: 0.4rem 0;
	}
	.thinking-label {
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
		font-style: italic;
	}
	.dot {
		width: 0.375rem;
		height: 0.375rem;
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
</style>
