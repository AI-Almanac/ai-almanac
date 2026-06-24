<script lang="ts">
	import { tick } from 'svelte';
	import type { ChatSessionState } from '$lib/chat/session.svelte';
	import { codeForToolCall, copyCode, formatToolName, renderMarkdown } from '$lib/chat/format';

	interface Props {
		chat: ChatSessionState;
		emptyMessage: string;
		suggestions: string[];
		onSuggestion: (text: string) => void;
		onOpenArtifact: (artifactId: string) => void;
	}

	const { chat, emptyMessage, suggestions, onSuggestion, onOpenArtifact }: Props = $props();

	let messagesEl = $state<HTMLElement | null>(null);
	let shownCode = $state<Set<string>>(new Set());

	function toggleCode(key: string) {
		const next = new Set(shownCode);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		shownCode = next;
	}

	// Scroll to bottom whenever messages update.
	$effect(() => {
		void chat.messages;
		void chat.streamingTurn;
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

	{#each chat.visibleTurns as msg, i}
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
					<button class="artifact-chip" onclick={() => onOpenArtifact(artifact.id)}>
						&#128444; {artifact.label ?? 'Figure'} &rarr;
					</button>
				{/each}
			{/each}
		{/if}
	{/each}

	{#if chat.pendingApproval && !chat.sending}
		{@const approval = chat.pendingApproval}
		<div class="approval-card">
			{#if approval.kind === 'benchmark'}
				<p class="approval-title">Ready to run benchmark</p>
				<p class="approval-subtitle">
					{approval.config.model_names?.join(', ') ?? 'Selected models'} ·
					{approval.config.region_name ?? 'Selected region'} · Days 1–{approval.config
						.forecast_window_days ?? 30}
				</p>
				<div class="approval-actions">
					<button class="approval-run" onclick={chat.approveSubmit}>Run benchmark</button>
					<button class="approval-cancel" onclick={chat.declineSubmit}>Not yet</button>
				</div>
			{:else}
				<p class="approval-title">Ready to train blend</p>
				<p class="approval-subtitle">
					{approval.config.model_names?.join(', ') ?? 'Selected models'} ·
					{approval.config.obs_dataset_name ?? 'Selected observations'} · Train {approval.config
						.training_years || '—'}
				</p>
				<div class="approval-actions">
					<button class="approval-run" onclick={chat.approveSubmit}>Train blend</button>
					<button class="approval-cancel" onclick={chat.declineSubmit}>Not yet</button>
				</div>
			{/if}
		</div>
	{/if}

	{#if chat.sending}
		{@const runningTool = chat.streamingTurn?.tool_calls?.find((tc) => tc.status === 'running')}
		{#if !chat.streamingTurn || runningTool}
			<div class="thinking">
				{#if runningTool}
					<span class="thinking-label">{formatToolName(runningTool.name)}…</span>
				{/if}
				<span class="dot"></span><span class="dot"></span><span class="dot"></span>
			</div>
		{/if}
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
</style>
