<script lang="ts">
	import { approvalCardCopy, approvalSummary, type PendingApproval } from '$lib/chat/approval';

	interface Props {
		approval: PendingApproval;
		onApprove: () => void;
		onDecline: () => void;
	}

	const { approval, onApprove, onDecline }: Props = $props();

	const copy = $derived(approvalCardCopy(approval));
	const summary = $derived(approvalSummary(approval));
	const facts = $derived(approval.kind === 'step' ? approval.facts : []);
	const warnings = $derived(approval.validation?.warnings ?? []);
</script>

<!-- What will run comes first, so the buttons are read after it. -->
<section class="confirmation" aria-label={copy.title}>
	<p class="title" role="status">
		<span class="icon" aria-hidden="true">▸</span>
		{copy.title}
	</p>
	{#if summary}
		<p class="summary">{summary}</p>
	{/if}
	{#if facts.length}
		<dl class="facts">
			{#each facts as fact, index (index)}
				<div class="fact">
					<dt>{fact.label}</dt>
					<dd>{fact.value}</dd>
				</div>
			{/each}
		</dl>
	{/if}
	{#if warnings.length}
		<ul class="warnings">
			{#each warnings as warning (warning)}
				<li>{warning}</li>
			{/each}
		</ul>
	{/if}
	<div class="actions">
		<button class="action primary" onclick={onApprove}>{copy.action}</button>
		<button class="action quiet" onclick={onDecline}>Not yet</button>
	</div>
</section>

<style>
	.confirmation {
		align-self: center;
		max-width: 100%;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.75rem;
		background: var(--color-surface);
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
	}
	.title {
		margin: 0;
		display: flex;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--text-size-label);
		font-weight: 600;
		color: var(--color-text);
	}
	.icon {
		font-weight: 700;
	}
	.summary {
		margin: 0;
	}
	.facts {
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.fact {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}
	.fact dt::after {
		content: ':';
	}
	.fact dd {
		margin: 0;
		color: var(--color-text);
		overflow-wrap: anywhere;
	}
	.warnings {
		margin: 0;
		padding: var(--space-2) var(--space-3) var(--space-2) var(--space-5);
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		border: 1px solid var(--color-status-running);
		border-radius: 0.6rem;
		background: var(--color-status-running-bg);
		color: var(--color-status-running);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}
	.action {
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--color-accent-border);
		border-radius: 2rem;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
		transition:
			background-color 0.12s,
			color 0.12s,
			border-color 0.12s;
	}
	.action.primary {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: var(--color-bg);
	}
	.action.primary:hover {
		filter: brightness(1.1);
	}
	.action.quiet {
		border-color: var(--color-border);
		background: transparent;
		color: var(--color-text-muted);
	}
	.action.quiet:hover {
		color: var(--color-text);
		border-color: var(--color-text-muted);
	}
</style>
