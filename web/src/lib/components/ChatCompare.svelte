<script lang="ts">
	import { continueComparison } from '$lib/api';
	import type { ComparisonState } from '$lib/chat/compare.svelte';

	interface Props {
		comparison: ComparisonState;
		onClose: () => void;
		/** Labeled mode (admin playground): arms are named up front, so the
		 * blind framing and post-vote reveal don't apply. */
		labeled?: boolean;
	}

	const { comparison, onClose, labeled = false }: Props = $props();

	let voteNote = $state('');
	let voting = $state(false);
	let followUpText = $state('');

	async function vote(winnerSessionId: string | null) {
		if (voting) return;
		voting = true;
		await comparison.vote(winnerSessionId, voteNote.trim() || undefined);
		voting = false;
	}

	function sendFollowUp() {
		const text = followUpText.trim();
		const id = comparison.comparisonId;
		if (!text || !id || comparison.running) return;
		followUpText = '';
		void comparison.followUp(text, continueComparison(id, text));
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			sendFollowUp();
		}
	}
</script>

<div class="compare-view">
	<p class="compare-hint">
		{#if labeled}
			Two rulesets answering side by side. Keep the conversation going: follow-ups reach both.
			Neither side can run anything, and one vote covers the whole exchange.
		{:else}
			Two assistant styles answering side by side — you won't see which is which until you vote.
			Keep the conversation going: follow-ups reach both. Neither side can run anything, and one
			vote covers the whole exchange.
		{/if}
	</p>

	{#if comparison.error}
		<p class="compare-error">{comparison.error}</p>
	{/if}

	{#if comparison.arms.length}
		<div class="board" style="--arms: {comparison.arms.length}">
			{#each comparison.arms as arm (arm.sessionId)}
				<h3 class="arm-head">
					{arm.label}
					{#if !labeled && comparison.voted && comparison.revealedName(arm.sessionId)}
						<span class="reveal">was {comparison.revealedName(arm.sessionId)}</span>
					{/if}
				</h3>
			{/each}
			{#each comparison.rounds as round, r (r)}
				<p class="round-message">{round.message}</p>
				{#each round.answers as answer, a (a)}
					<article class="cell">
						{#if answer.cautions.length}
							<ul class="cautions">
								{#each answer.cautions as caution (caution)}
									<li>{caution}</li>
								{/each}
							</ul>
						{/if}
						{#if answer.tools.length}
							<p class="tools">
								Looked at data: {answer.tools.length} tool call{answer.tools.length === 1
									? ''
									: 's'}
							</p>
						{/if}
						<pre class="answer">{answer.text}{comparison.running &&
							r === comparison.rounds.length - 1 &&
							!answer.text
								? '…'
								: ''}</pre>
						{#if answer.error}<p class="compare-error">{answer.error}</p>{/if}
					</article>
				{/each}
			{/each}
		</div>
	{/if}

	{#if !comparison.voted && comparison.comparisonId}
		<div class="follow-up-row">
			<textarea
				bind:value={followUpText}
				onkeydown={handleKeydown}
				placeholder="Ask a follow-up — it goes to both…"
				rows={1}
				disabled={comparison.running}></textarea>
			<button onclick={sendFollowUp} disabled={comparison.running || !followUpText.trim()}>
				{comparison.running ? '…' : 'Send to both'}
			</button>
		</div>
	{/if}

	<div class="compare-actions">
		{#if !comparison.running && comparison.rounds.length && !comparison.voted}
			<input bind:value={voteNote} placeholder="Why? (optional)" maxlength="2000" />
			<button onclick={() => void vote(comparison.arms[0].sessionId)} disabled={voting}>
				A is better
			</button>
			<button onclick={() => void vote(null)} disabled={voting}>Tie</button>
			<button onclick={() => void vote(comparison.arms[1]?.sessionId ?? null)} disabled={voting}>
				B is better
			</button>
		{/if}
		{#if comparison.voted}
			<span class="thanks">Thanks — your vote covers the whole conversation.</span>
		{/if}
		{#if !comparison.running}
			<button class="close-btn" onclick={onClose}>
				{labeled ? 'Discard comparison' : 'Back to chat'}
			</button>
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
	.board {
		display: grid;
		/* Side by side when each answer has room to read, stacked when it does
		   not — two 12rem ribbons are worse than one column you scroll. */
		grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr));
		gap: 0.6rem;
	}
	.arm-head {
		margin: 0;
		font-size: 0.8rem;
		display: flex;
		align-items: baseline;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	.round-message {
		grid-column: 1 / -1;
		margin: 0;
		padding: 0.45rem 0.6rem;
		border-radius: 0.4rem;
		background: var(--color-accent-light);
		border: 1px solid var(--color-accent-border);
		font-size: 0.8rem;
	}
	.cell {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		padding: 0.6rem;
		background: var(--color-surface);
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
	.follow-up-row {
		display: flex;
		gap: 0.5rem;
	}
	.follow-up-row textarea {
		flex: 1;
		min-width: 0;
		resize: none;
		padding: 0.45rem 0.6rem;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface);
		font-size: 0.8rem;
		font-family: inherit;
		line-height: 1.4;
	}
	.follow-up-row button {
		padding: 0.4rem 0.8rem;
		border-radius: 0.35rem;
		border: 1px solid var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
		font-size: 0.8rem;
		font-weight: 600;
		cursor: pointer;
		white-space: nowrap;
		align-self: flex-end;
	}
	.follow-up-row button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
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
