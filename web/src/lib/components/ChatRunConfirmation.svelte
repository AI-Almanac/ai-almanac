<script lang="ts">
	import type { ChatSessionState } from '$lib/chat/session.svelte';
	import ChatNotice from '$lib/components/ChatNotice.svelte';

	type Approval = NonNullable<ChatSessionState['pendingApproval']>;

	interface Props {
		approval: Approval;
		onApprove: () => void;
		onDecline: () => void;
	}

	const { approval, onApprove, onDecline }: Props = $props();

	function planSummary(plan: Approval): string {
		const models = plan.config.model_names?.join(', ') || 'Selected models';
		if (plan.kind === 'benchmark') {
			const region = plan.config.region_name ?? 'Selected region';
			return `${models} · ${region} · Days 1–${plan.config.forecast_window_days ?? 30}`;
		}
		const observations = plan.config.obs_dataset_name ?? 'Selected observations';
		return `${models} · ${observations} · Train ${plan.config.training_years || '—'}`;
	}

	const isBenchmark = $derived(approval.kind === 'benchmark');
	const warnings = $derived(approval.validation?.warnings ?? []);
</script>

<div class="confirmation">
	<ChatNotice
		icon="▸"
		title={isBenchmark ? 'Ready to run' : 'Ready to train'}
		detail={planSummary(approval)}
		actions={[
			{
				label: isBenchmark ? 'Run benchmark' : 'Train blend',
				onclick: onApprove,
				emphasis: 'primary'
			},
			{ label: 'Not yet', onclick: onDecline, emphasis: 'quiet' }
		]}
	/>
	{#if warnings.length}
		<ul class="warnings">
			{#each warnings as warning (warning)}
				<li>{warning}</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.confirmation {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.4rem;
	}
	.warnings {
		margin: 0;
		padding: 0.5rem 0.75rem 0.5rem 1.5rem;
		max-width: 100%;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		border: 1px solid var(--color-status-running);
		border-radius: 0.6rem;
		background: var(--color-status-running-bg);
		color: var(--color-status-running);
		font-size: 0.75rem;
	}
</style>
