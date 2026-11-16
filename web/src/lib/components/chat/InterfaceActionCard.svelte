<script lang="ts">
	import { actionFromToolCall, actionLabel } from '$lib/interface-actions/actions';
	import type { ResultCardProps } from './step-context';

	const { call, context }: ResultCardProps = $props();

	const action = $derived(actionFromToolCall(call));
	const act = $derived(context.onInterfaceAction);
</script>

<!-- What the assistant offered, kept in the conversation as the way to bring it up again. -->
{#if action && act}
	<button type="button" class="action" onclick={() => act(action, 'user')}>
		<span class="icon" aria-hidden="true">↗</span>
		{actionLabel(action)}
	</button>
{/if}

<style>
	.action {
		display: inline-flex;
		align-items: center;
		align-self: flex-start;
		gap: var(--space-2);
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.375rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
		transition:
			border-color 0.12s,
			background-color 0.12s;
	}
	.action:hover,
	.action:focus-visible {
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}
	.icon {
		color: var(--color-accent);
	}
</style>
