<script lang="ts">
	import type { IntegrityStatus } from '$lib/workspace/lineage';

	interface Props {
		status: IntegrityStatus | null;
		checking: boolean;
		onCheck: () => void;
	}

	let { status, checking, onCheck }: Props = $props();

	const MARK = { ok: '✓', problem: '⚠', neutral: '·' } as const;
	// A passing check needs no second look, so its re-check waits for a hover or focus.
	const quiet = $derived(!checking && status?.tone === 'ok');
</script>

<span class="integrity" class:quiet role="status">
	{#if checking}
		<span class="state neutral">Checking against the record…</span>
	{:else if status}
		<span class="state {status.tone}">
			<span aria-hidden="true">{MARK[status.tone]}</span>
			{status.label}
		</span>
	{/if}
	<button
		type="button"
		class="check"
		disabled={checking}
		onclick={onCheck}
		title="Re-read the files and compare them with how this file was recorded"
	>
		Verify again
	</button>
</span>

<style>
	.integrity {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: baseline;
		row-gap: 0.2rem;
		font-size: inherit;
	}
	.state {
		display: inline-flex;
		align-items: baseline;
		gap: 0.3rem;
		color: var(--color-text-muted);
	}
	.state.ok {
		color: var(--color-status-complete);
	}
	.state.problem {
		color: var(--color-status-failed);
		font-weight: 600;
	}
	.check {
		padding: 0;
		margin-left: 0.5em;
		border: 0;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		text-decoration: underline;
		text-decoration-color: var(--color-border);
		text-underline-offset: 0.15em;
		white-space: nowrap;
		cursor: pointer;
	}
	.check:hover,
	.check:focus-visible {
		color: var(--color-accent);
	}
	.check:disabled {
		opacity: 0.6;
		cursor: default;
	}
	/* Collapsed rather than hidden, so the keyboard still reaches it and focus shows it. */
	@media (hover: hover) {
		.quiet .check {
			max-width: 0;
			margin-left: 0;
			overflow: hidden;
			opacity: 0;
		}
		.quiet:hover .check,
		.quiet:focus-within .check {
			max-width: 10em;
			margin-left: 0.5em;
			opacity: 1;
		}
	}
</style>
