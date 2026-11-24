<script lang="ts">
	import { goto } from '$app/navigation';
	import { account } from '$lib/account.svelte';
	import { failureMessage, listWorkspaces, startBlankWorkspace } from '$lib/api';
	import { lastOpenedWorkspace, workspaceToOpen } from '$lib/workspace/last-opened';

	let error = $state<string | null>(null);
	let started = false;

	$effect(() => {
		if (!account.loaded || started) return;
		started = true;
		if (!account.canUseWorkspaces) goto('/');
		else void openWorkspace();
	});

	/** Where the user left off, or a blank workspace when they have none yet. */
	async function openWorkspace() {
		try {
			const listed = await listWorkspaces();
			const remembered = lastOpenedWorkspace(localStorage, account.account?.id ?? null);
			const id = workspaceToOpen(remembered, listed) ?? (await startBlankWorkspace()).id;
			await goto(`/workspace/${id}`, { replaceState: true });
		} catch (e) {
			error = failureMessage(e);
		}
	}
</script>

<div class="opening">
	{#if error}
		<p class="error" role="alert">{error}</p>
	{:else}
		<p class="muted">Opening your workspace…</p>
	{/if}
</div>

<style>
	.opening {
		display: flex;
		justify-content: center;
		padding: var(--space-5) var(--space-4);
	}
	.muted {
		margin: 0;
		color: var(--color-text-muted);
	}
	.error {
		margin: 0;
		color: var(--color-danger);
	}
</style>
