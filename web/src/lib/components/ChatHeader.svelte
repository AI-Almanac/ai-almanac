<script lang="ts">
	import { onMount } from 'svelte';
	import type { ChatSession } from '$lib/api';
	import type { ChatSessionState } from '$lib/chat/session.svelte';
	import { sessionLabel } from '$lib/chat/format';

	interface Props {
		chat: ChatSessionState;
	}

	const { chat }: Props = $props();

	let showSessionList = $state(false);
	let renamingSessionId = $state<string | null>(null);
	let renamingValue = $state('');
	let savingTitle = $state(false);
	let copyState = $state<'idle' | 'copied'>('idle');

	function handleOutsideClick(e: MouseEvent) {
		if (showSessionList && !(e.target as Element).closest('.session-selector')) {
			showSessionList = false;
		}
	}

	onMount(() => {
		document.addEventListener('click', handleOutsideClick, true);
		return () => document.removeEventListener('click', handleOutsideClick, true);
	});

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
		const renamed = await chat.renameSession(renamingSessionId, renamingValue);
		savingTitle = false;
		if (renamed) cancelRename();
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

	async function selectSession(id: string) {
		await chat.loadSession(id);
		showSessionList = false;
	}

	async function createSession() {
		await chat.createNewSession();
		showSessionList = false;
	}

	function handleDelete(id: string, e: MouseEvent) {
		e.stopPropagation();
		void chat.deleteSession(id);
	}

	async function copyChat() {
		if (chat.messages.length === 0) return;
		const text = chat.messages
			.map((m) => `${m.role === 'user' ? 'You' : 'AI'}: ${m.content}`)
			.join('\n\n');
		await navigator.clipboard.writeText(text);
		copyState = 'copied';
		setTimeout(() => (copyState = 'idle'), 1500);
	}
</script>

<div class="chat-header">
	<span class="ai-badge">✦ AI</span>
	<span class="beta-badge" title="The assistant is under active development and can be wrong.">
		Beta
	</span>
	<div class="session-selector">
		{#if chat.currentSession && renamingSessionId === chat.currentSession.id}
			<div class="session-rename-bar">
				<input
					class="session-rename-input"
					bind:value={renamingValue}
					onkeydown={handleRenameKeydown}
					placeholder={sessionLabel(chat.currentSession)}
					maxlength="140"
				/>
				<button class="session-action-btn" onclick={() => void saveRename()} disabled={savingTitle}>
					{savingTitle ? 'Saving...' : 'Save'}
				</button>
				<button class="session-action-btn secondary" onclick={cancelRename} disabled={savingTitle}>
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
					{#if chat.currentSession}
						{sessionLabel(chat.currentSession)}
					{:else}
						AI Analysis
					{/if}
				</span>
				<span class="session-chevron" class:open={showSessionList}>▾</span>
			</button>
		{/if}

		{#if showSessionList}
			<div class="session-dropdown">
				<button class="session-new-btn" onclick={createSession}> + New Chat </button>
				{#if chat.sessions.length > 0}
					<div class="session-divider"></div>
					{#each chat.sessions as s}
						<div class="session-item" class:active={s.id === chat.sessionId}>
							<button class="session-item-btn" onclick={() => selectSession(s.id)}>
								<span class="session-item-title">{sessionLabel(s)}</span>
								<span class="session-item-meta"
									>{s.message_count} msg · {new Date(s.updated_at).toLocaleDateString()}</span
								>
							</button>
							<button class="session-item-rename" title="Rename" onclick={(e) => beginRename(s, e)}
								>Rename</button
							>
							<button
								class="session-item-delete"
								title="Delete"
								onclick={(e) => handleDelete(s.id, e)}>&times;</button
							>
						</div>
					{/each}
				{/if}
			</div>
		{/if}
	</div>

	<div class="header-actions">
		{#if chat.currentSession && renamingSessionId !== chat.currentSession.id}
			<button
				class="copy-btn"
				onclick={(e) => beginRename(chat.currentSession!, e)}
				title="Rename chat"
			>
				Rename
			</button>
		{/if}
		<button
			class="copy-btn"
			onclick={copyChat}
			disabled={chat.messages.length === 0}
			title="Copy chat to clipboard"
		>
			{copyState === 'copied' ? '✓ Copied' : 'Copy'}
		</button>
	</div>
</div>

<style>
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
	.beta-badge {
		font-size: 0.6rem;
		font-weight: 700;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--color-status-running);
		background: var(--color-status-running-bg);
		border: 1px solid var(--color-status-running);
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
</style>
