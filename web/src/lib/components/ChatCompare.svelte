<script lang="ts">
	import type { ComparisonState } from '$lib/chat/compare.svelte';

	interface Props {
		comparison: ComparisonState;
		onClose: () => void;
	}

	const { comparison, onClose }: Props = $props();

	let voteNote = $state('');
	let voting = $state(false);

	async function vote(winnerSessionId: string | null) {
		if (voting) return;
		voting = true;
		await comparison.vote(winnerSessionId, voteNote.trim() || undefined);
		voting = false;
	}
</script>

<div class="compare-view">
	<p class="compare-hint">
		Two answers to your question, from two assistant styles — you won't see which is which until you
		vote. Neither answer can run anything. Your vote helps decide which style the assistant keeps.
	</p>

	{#if comparison.error}
		<p class="compare-error">{comparison.error}</p>
	{/if}

	<div class="columns">
		{#each comparison.columns as column (column.sessionId)}
			<article class="column">
				<h3 class="column-head">
					{column.label}
					{#if comparison.voted && comparison.revealedName(column.sessionId)}
						<span class="reveal">was {comparison.revealedName(column.sessionId)}</span>
					{/if}
				</h3>
				{#if column.cautions.length}
					<ul class="cautions">
						{#each column.cautions as caution (caution)}
							<li>{caution}</li>
						{/each}
					</ul>
				{/if}
				{#if column.tools.length}
					<p class="tools">
						Looked at data: {column.tools.length} tool call{column.tools.length === 1 ? '' : 's'}
					</p>
				{/if}
				<pre class="answer">{column.text}{comparison.running && !column.text ? '…' : ''}</pre>
				{#if column.error}<p class="compare-error">{column.error}</p>{/if}
			</article>
		{/each}
	</div>

	<div class="compare-actions">
		{#if !comparison.running && comparison.columns.length && !comparison.voted}
			<input bind:value={voteNote} placeholder="Why? (optional)" maxlength="2000" />
			<button onclick={() => void vote(comparison.columns[0].sessionId)} disabled={voting}>
				A is better
			</button>
			<button onclick={() => void vote(null)} disabled={voting}>Tie</button>
			<button onclick={() => void vote(comparison.columns[1]?.sessionId ?? null)} disabled={voting}>
				B is better
			</button>
		{/if}
		{#if comparison.voted}
			<span class="thanks">Thanks — your vote was recorded.</span>
		{/if}
		{#if !comparison.running}
			<button class="close-btn" onclick={onClose}>Back to chat</button>
		{/if}
	</div>
</div>

<style>
	.compare-view {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		padding: 0.75rem;
		overflow-y: auto;
		flex: 1;
		min-height: 0;
	}
	.compare-hint {
		margin: 0;
		font-size: 0.78rem;
		color: var(--color-text-muted);
		line-height: 1.45;
	}
	.compare-error {
		margin: 0;
		padding: 0.5rem 0.65rem;
		border-radius: 0.4rem;
		font-size: 0.78rem;
		color: var(--color-status-failed);
		background: var(--color-status-failed-bg);
		border: 1px solid var(--color-status-failed);
	}
	.columns {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
	}
	.column {
		flex: 1 1 16rem;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		padding: 0.6rem;
		background: var(--color-surface);
	}
	.column-head {
		margin: 0;
		font-size: 0.8rem;
		display: flex;
		align-items: baseline;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	.reveal {
		font-weight: 400;
		font-size: 0.72rem;
		color: var(--color-accent);
	}
	.cautions {
		margin: 0;
		padding-left: 1.1rem;
		font-size: 0.72rem;
		color: var(--color-status-running);
	}
	.tools {
		margin: 0;
		font-size: 0.68rem;
		color: var(--color-text-muted);
	}
	.answer {
		margin: 0;
		white-space: pre-wrap;
		font-family: inherit;
		font-size: 0.8rem;
		line-height: 1.55;
	}
	.compare-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-items: center;
	}
	.compare-actions input {
		flex: 1 1 12rem;
		min-width: 0;
		padding: 0.4rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface);
		font-size: 0.8rem;
		font-family: inherit;
	}
	.compare-actions button {
		padding: 0.4rem 0.8rem;
		border-radius: 0.35rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		font-size: 0.8rem;
		cursor: pointer;
	}
	.compare-actions button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.close-btn {
		margin-left: auto;
	}
	.thanks {
		font-size: 0.78rem;
		color: var(--color-accent);
	}
</style>
