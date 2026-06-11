<script lang="ts">
	import { getJobLogs } from '$lib/api';

	type Props = { jobId: string };
	let { jobId }: Props = $props();

	let logs = $state<string | null>(null);
	let expanded = $state(false);
	let loading = $state(false);

	async function toggle() {
		expanded = !expanded;
		if (expanded && logs === null) {
			loading = true;
			try {
				const res = await getJobLogs(jobId);
				logs = res.logs;
			} catch (e: any) {
				logs = `Failed to fetch logs: ${e.message}`;
			} finally {
				loading = false;
			}
		}
	}
</script>

<button class="toggle-btn" onclick={toggle}>
	{expanded ? '▲ Hide logs' : '▼ Show logs'}
</button>

{#if expanded}
	<div class="log-box">
		{#if loading}
			<span class="dim">Loading…</span>
		{:else}
			<pre>{logs ?? '(empty)'}</pre>
		{/if}
	</div>
{/if}

<style>
	.toggle-btn {
		background: none;
		border: none;
		color: var(--color-text-dim);
		font-size: 0.75rem;
		cursor: pointer;
		padding: 0.25rem 0;
		text-decoration: underline;
	}
	.toggle-btn:hover {
		color: var(--color-text);
	}

	.log-box {
		margin-top: 0.4rem;
		background: var(--color-surface-raised, #1a1a1a);
		border: 1px solid var(--color-border-subtle);
		border-radius: 4px;
		padding: 0.75rem 1rem;
		max-height: clamp(15rem, 45vh, 25rem);
		overflow-y: auto;
	}
	.log-box pre {
		margin: 0;
		font-size: 0.72rem;
		line-height: 1.5;
		white-space: pre-wrap;
		word-break: break-all;
		color: var(--color-text);
		font-family: 'JetBrains Mono', 'Fira Mono', monospace;
	}
	.dim {
		color: var(--color-text-dim);
		font-size: 0.8rem;
	}
</style>
