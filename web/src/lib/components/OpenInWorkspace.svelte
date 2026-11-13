<script lang="ts">
	import { failureMessage } from '$lib/api/core';
	import { goto } from '$app/navigation';
	import { promoteSessionToWorkspace } from '$lib/api';

	interface Props {
		sessionId: string | null;
	}

	let { sessionId }: Props = $props();
	let busy = $state(false);
	let error = $state<string | null>(null);

	async function open() {
		if (!sessionId) return;
		busy = true;
		error = null;
		try {
			const workspace = await promoteSessionToWorkspace(sessionId);
			await goto(`/workspace/${workspace.id}`);
		} catch (e) {
			error = failureMessage(e);
		} finally {
			busy = false;
		}
	}
</script>

<div class="open-in-workspace">
	<button type="button" onclick={open} disabled={busy || !sessionId}>
		{busy ? 'Opening…' : 'Open in workspace'}
	</button>
	{#if error}<span class="error">{error}</span>{/if}
</div>

<style>
	.open-in-workspace {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		justify-content: flex-end;
	}
	button {
		padding: 0.4rem 0.8rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text);
		font-size: 0.82rem;
		cursor: pointer;
	}
	button:hover:not(:disabled) {
		border-color: var(--color-accent-border);
	}
	button:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.error {
		color: var(--color-danger);
		font-size: 0.8rem;
	}
</style>
