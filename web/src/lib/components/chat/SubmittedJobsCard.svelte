<script lang="ts">
	import { allJobsComplete, liveJobStatus, submittedJobs } from '$lib/chat/tool-results';
	import { statusName } from '$lib/run-status';
	import type { ResultCardProps } from './step-context';

	const { call, context }: ResultCardProps = $props();

	const jobs = $derived(submittedJobs(call.result));
	const finished = $derived(allJobsComplete(jobs, context.jobStatuses));

	function viewResults() {
		context.onOpenItem?.({ kind: 'run', jobIds: jobs.map((job) => job.id) });
	}
</script>

{#if jobs.length > 0}
	<div class="card">
		<p class="title">{jobs.length === 1 ? '1 run submitted' : `${jobs.length} runs submitted`}</p>
		<ul>
			{#each jobs as job (job.id)}
				{@const status = liveJobStatus(job, context.jobStatuses)}
				<li>
					<span class="name">{job.label}</span>
					<span class="status status-{status}">{statusName(status)}</span>
				</li>
			{/each}
		</ul>
		{#if finished && context.onOpenItem}
			<button class="view" onclick={viewResults}>View results</button>
		{/if}
	</div>
{/if}

<style>
	.card {
		margin-left: 1.5rem;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		padding: 0.6rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-surface);
		font-size: 0.78rem;
	}
	.title {
		margin: 0;
		font-weight: 600;
	}
	ul {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	li {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 0.5rem;
	}
	.name {
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.status {
		flex-shrink: 0;
		font-size: 0.68rem;
		font-weight: 600;
		padding: 0.1rem 0.45rem;
		border-radius: 1rem;
		color: var(--color-status-running);
		background: var(--color-status-running-bg);
	}
	.status-complete {
		color: var(--color-status-complete);
		background: var(--color-status-complete-bg);
	}
	.status-failed,
	.status-canceled {
		color: var(--color-status-failed);
		background: var(--color-status-failed-bg);
	}
	.view {
		align-self: flex-start;
		border: 0;
		border-radius: 0.35rem;
		background: var(--color-accent);
		color: var(--color-bg);
		padding: 0.35rem 0.8rem;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	.view:hover {
		background: var(--color-accent-hover);
	}
</style>
