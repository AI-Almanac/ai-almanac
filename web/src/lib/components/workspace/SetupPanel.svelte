<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		/** What the form starts from, under the title. */
		note?: string | null;
		/** Something went wrong after the form was sent. */
		error?: string | null;
		/** The form's choices are still being fetched. */
		loading?: boolean;
		onCancel: () => void;
		children: Snippet;
	}

	let { title, note = null, error = null, loading = false, onCancel, children }: Props = $props();

	const headingId = $props.id();
</script>

<!-- A setup form over the stage: its title, what it starts from, and a way out. -->
<section class="setup-panel" aria-labelledby={headingId}>
	<header class="head">
		<div>
			<h2 id={headingId}>{title}</h2>
			{#if note}<p class="note">{note}</p>{/if}
		</div>
		<button type="button" class="close" onclick={onCancel}>Cancel</button>
	</header>
	{#if error}<p class="error" role="alert">{error}</p>{/if}
	{#if loading}
		<p class="note">Loading…</p>
	{:else}
		{@render children()}
	{/if}
</section>

<style>
	.setup-panel {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		width: min(100%, 44rem);
		margin: 0 auto;
	}
	.head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--space-3);
	}
	h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-title);
	}
	.note {
		margin: var(--space-1) 0 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
	.close {
		flex: none;
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
	}
	.error {
		margin: 0;
		color: var(--color-danger);
		font-size: var(--text-size-label);
	}
</style>
