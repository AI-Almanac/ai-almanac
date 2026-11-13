<script lang="ts">
	import type { FollowUp } from '$lib/api';

	interface Props {
		followUps: FollowUp[];
		onCancel?: (followUpId: string) => void;
	}

	let { followUps, onCancel }: Props = $props();

	function waitingOn(followUp: FollowUp): string {
		const count = followUp.job_ids.length;
		return `after ${count} run${count === 1 ? '' : 's'} finish`;
	}
</script>

<ul class="queued">
	{#each followUps as followUp (followUp.id)}
		<li>
			<span class="state">{followUp.status === 'running' ? 'Continuing now' : 'Queued'}</span>
			<span class="instruction" title={followUp.instruction}>{followUp.instruction}</span>
			{#if followUp.status === 'waiting'}
				<span class="muted">{waitingOn(followUp)}</span>
				{#if onCancel}
					<button onclick={() => onCancel(followUp.id)}>Cancel</button>
				{/if}
			{/if}
		</li>
	{/each}
</ul>

<style>
	.queued {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin: 0;
		padding: 0.5rem 0.85rem;
		list-style: none;
		border-top: 1px solid var(--color-border);
		font-size: 0.8rem;
	}

	li {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		min-width: 0;
	}

	.state {
		flex-shrink: 0;
		font-weight: 600;
	}

	.instruction {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.muted {
		flex-shrink: 0;
		color: var(--color-text-muted);
	}

	button {
		flex-shrink: 0;
		border: none;
		background: none;
		color: var(--color-text-muted);
		font-size: inherit;
		text-decoration: underline;
		cursor: pointer;
	}
</style>
