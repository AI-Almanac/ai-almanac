<script lang="ts">
	import { tick } from 'svelte';
	import type { ChatSessionState } from '$lib/chat/session.svelte';
	import ChatActivity from '$lib/components/ChatActivity.svelte';
	import ChatJobNotice from '$lib/components/ChatJobNotice.svelte';
	import ChatRunConfirmation from '$lib/components/ChatRunConfirmation.svelte';
	import { conversationItems } from '$lib/chat/conversation';
	import { renderMarkdown } from '$lib/chat/format';
	import { turnTimeline } from '$lib/chat/timeline';
	import { rateChatTurn } from '$lib/api';

	interface Props {
		chat: ChatSessionState;
		emptyMessage: string;
		suggestions: string[];
		/** Comparison mode is the opt-in for all feedback UI, thumbs included. */
		canRate?: boolean;
		onSuggestion: (text: string) => void;
		onOpenArtifact: (artifactId: string) => void;
	}

	const {
		chat,
		emptyMessage,
		suggestions,
		canRate = false,
		onSuggestion,
		onOpenArtifact
	}: Props = $props();

	let messagesEl = $state<HTMLElement | null>(null);

	const conversation = $derived(
		conversationItems(chat.visibleTurns, chat.jobActivity.events, !chat.sending)
	);
	let ratings = $state<Record<string, 1 | -1>>({});

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

	// Scroll to bottom whenever messages update.
	$effect(() => {
		void chat.messages;
		void chat.streamingTurn;
		void chat.jobActivity;
		tick().then(() => {
			if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
		});
	});
</script>

<div class="messages" bind:this={messagesEl}>
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
			{#if msg.role === 'user'}
				<div class="message user">
					<div class="message-content">{msg.content}</div>
				</div>
			{:else}
				{@const timeline = turnTimeline(msg)}
				{@const streaming = msg.id === chat.streamingTurn?.id}
				{#each timeline as item, ii (item.key)}
					{#if item.kind === 'text'}
						<div class="message assistant">
							<div class="message-content prose">{@html renderMarkdown(item.text)}</div>
						</div>
					{:else}
						<ChatActivity
							group={item}
							live={streaming && ii === timeline.length - 1}
							{onOpenArtifact}
						/>
					{/if}
				{/each}

				{#if canRate && msg.content && msg.id !== chat.streamingTurn?.id}
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
				{#each msg.guardrails ?? [] as notice}
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
			<span class="dot"></span><span class="dot"></span><span class="dot"></span>
		</div>
	{/if}
</div>

<style>
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

	.rating {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.72rem;
		color: var(--color-text-muted);
	}
	.rating-btn {
		background: none;
		border: 1px solid transparent;
		border-radius: 0.3rem;
		cursor: pointer;
		padding: 0.1rem 0.25rem;
		font-size: 0.8rem;
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
		padding: 0.6rem 0.75rem;
		font-size: 0.8rem;
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
		gap: 6px;
		padding: 0.4rem 0;
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
</style>
