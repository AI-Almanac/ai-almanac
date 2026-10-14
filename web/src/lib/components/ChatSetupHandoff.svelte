<script lang="ts">
	import type { SetupHandoff } from '$lib/chat/handoff';

	const { handoff }: { handoff: SetupHandoff } = $props();

	const label = $derived(
		handoff.workflow === 'benchmark' ? 'Continue to benchmark setup' : 'Continue to blend setup'
	);
</script>

<a class="setup-handoff" href={handoff.href}>
	<span class="label">{label} →</span>
	<span class="request">{handoff.request}</span>
</a>

<style>
	.setup-handoff {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		align-self: flex-start;
		max-width: 100%;
		border: 1px solid var(--color-accent);
		border-radius: 0.5rem;
		padding: 0.65rem 0.85rem;
		color: inherit;
		text-decoration: none;
	}

	.setup-handoff:hover {
		background: color-mix(in srgb, var(--color-accent) 8%, transparent);
	}

	.label {
		color: var(--color-accent);
		font-weight: 700;
	}

	.request {
		color: var(--color-text-muted);
		font-size: 0.9em;
	}
</style>
