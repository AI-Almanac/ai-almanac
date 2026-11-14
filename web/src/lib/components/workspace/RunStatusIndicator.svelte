<script lang="ts">
	import { runStatusWords } from '$lib/workspace/run-lifecycle';
	import type { Run } from '$lib/workspace/runs';

	interface Props {
		run: Run;
		/** In a list, a finished run is the expected case, so it shows only its dot. */
		quiet?: boolean;
	}

	let { run, quiet = false }: Props = $props();

	const status = $derived(runStatusWords(run));
	const showLabel = $derived(!quiet || status.tone !== 'complete');
</script>

<span class="status {status.tone}" title={status.label} aria-label={status.label} role="img">
	<span class="dot" aria-hidden="true"></span>
	{#if showLabel}<span class="label" aria-hidden="true">{status.label}</span>{/if}
</span>

<style>
	.status {
		display: inline-flex;
		flex: none;
		align-items: center;
		gap: 0.35rem;
		font-size: var(--text-size-caption);
		white-space: nowrap;
	}
	.dot {
		width: 0.45rem;
		height: 0.45rem;
		border-radius: 50%;
		background: currentColor;
	}
	.running {
		color: var(--color-status-running);
	}
	.running .dot {
		animation: pulse 1.6s ease-in-out infinite;
	}
	.complete {
		color: var(--color-status-complete);
	}
	.failed {
		color: var(--color-status-failed);
	}
	@keyframes pulse {
		50% {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.running .dot {
			animation: none;
		}
	}
</style>
