<script lang="ts">
	import { continueComparison } from '$lib/api';
	import { renderMarkdown } from '$lib/chat/format';
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
					<!-- Stacked one per row, the heading row above is too far away to say
					     which answer this is, so each cell carries its own label. -->
					{@const cellArm = comparison.arms[a]}
					<article class="cell">
						<h4 class="cell-arm">
							{cellArm?.label ?? `Answer ${a + 1}`}
							{#if cellArm && !labeled && comparison.voted && comparison.revealedName(cellArm.sessionId)}
								<span class="reveal">was {comparison.revealedName(cellArm.sessionId)}</span>
							{/if}
						</h4>
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
						{#if answer.text}
							<!-- Same renderer (and DOMPurify sanitising) the chat transcript
							     uses: answers arrive as markdown, tables and all. -->
							<div class="answer prose">{@html renderMarkdown(answer.text)}</div>
						{:else if comparison.running && r === comparison.rounds.length - 1}
							<p class="answer waiting">…</p>
						{/if}
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
		/* The panel is user-resizable, so the board reacts to its own width
		   rather than the viewport's. */
		container-type: inline-size;
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
		/* One track per arm, each an equal share of the panel. Not auto-fit: with
		   room for three 19rem tracks it made three, and two answers then filled
		   two of them and left the rest of the panel empty. */
		grid-template-columns: repeat(var(--arms), minmax(0, 1fr));
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
		font-size: 0.8rem;
		line-height: 1.55;
		min-width: 0;
	}
	.answer.waiting {
		color: var(--color-text-muted);
	}

	/* Markdown prose. Answers are compared on how they explain themselves, so a
	   table has to read as a table here as much as in the transcript. */
	.prose :global(p) {
		margin: 0 0 0.6rem;
	}
	.prose :global(p:last-child) {
		margin-bottom: 0;
	}
	.prose :global(ul),
	.prose :global(ol) {
		margin: 0 0 0.6rem;
		padding-left: 1.1rem;
	}
	.prose :global(li) {
		margin-bottom: 0.2rem;
	}
	.prose :global(h1),
	.prose :global(h2),
	.prose :global(h3),
	.prose :global(h4) {
		margin: 0.7rem 0 0.35rem;
		font-size: 0.85rem;
	}
	.prose :global(code) {
		padding: 0.05rem 0.25rem;
		border-radius: 0.2rem;
		background: var(--color-surface-muted, var(--color-surface));
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.92em;
	}
	.prose :global(pre) {
		margin: 0 0 0.6rem;
		padding: 0.5rem;
		overflow-x: auto;
		border-radius: 0.3rem;
		background: var(--color-surface-muted, var(--color-surface));
	}
	.prose :global(pre code) {
		padding: 0;
		background: none;
	}
	/* Tables are common in these answers and are what overflowed before. */
	.prose :global(table) {
		display: block;
		width: 100%;
		overflow-x: auto;
		border-collapse: collapse;
		margin: 0 0 0.6rem;
		font-size: 0.74rem;
	}
	.prose :global(th),
	.prose :global(td) {
		padding: 0.2rem 0.45rem 0.2rem 0;
		text-align: left;
		white-space: nowrap;
		border-bottom: 1px solid var(--color-border);
	}
	.prose :global(blockquote) {
		margin: 0 0 0.6rem;
		padding-left: 0.6rem;
		border-left: 2px solid var(--color-border);
		color: var(--color-text-muted);
	}

	/* Side by side, the heading row labels the columns and the per-cell label
	   would just repeat it every round. */
	.cell-arm {
		display: none;
		margin: 0;
		font-size: 0.78rem;
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
	/* Too narrow to read two columns: stack them rather than shrink to ribbons.
	   Stacked, the column headings no longer sit above their answers, so they
	   hand off to the per-cell labels. */
	@container (max-width: 34rem) {
		.board {
			grid-template-columns: minmax(0, 1fr);
		}
		.arm-head {
			display: none;
		}
		.cell-arm {
			display: block;
		}
	}
</style>
