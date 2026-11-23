<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { account } from '$lib/account.svelte';
	import { createWorkspace, failureMessage, listWorkspaces, type Workspace } from '$lib/api';
	import { getDefaultSeason, type WorkspaceSettings } from '$lib/api/workspace-settings';
	import NewWorkspaceForm from '$lib/components/workspace/NewWorkspaceForm.svelte';

	$effect(() => {
		if (account.loaded && !account.canUseWorkspaces) goto('/');
	});

	let workspaces = $state<Workspace[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let creatingNew = $state(false);

	onMount(async () => {
		try {
			workspaces = await listWorkspaces();
		} catch (e) {
			error = failureMessage(e);
		} finally {
			loading = false;
		}
	});

	async function create(name: string, description: string, settings?: WorkspaceSettings) {
		const created = await createWorkspace(name, description, settings);
		await goto(`/workspace/${created.id}`);
	}
</script>

<div class="page">
	<header>
		<h1>Workspaces</h1>
		<p class="muted">
			A workspace is a project: one conversation history, the benchmarks and blends you run in it,
			and every figure or dataset produced along the way, each with a verifiable record of how it
			was made.
		</p>
	</header>

	{#if creatingNew}
		<div class="card">
			<NewWorkspaceForm
				usualSeason={getDefaultSeason}
				onCreate={create}
				onCancel={() => (creatingNew = false)}
			/>
		</div>
	{:else}
		<div>
			<button type="button" onclick={() => (creatingNew = true)}>New workspace</button>
		</div>
	{/if}

	{#if error}
		<p class="error">{error}</p>
	{/if}

	{#if loading}
		<p class="muted">Loading…</p>
	{:else if workspaces.length === 0}
		<p class="muted">No workspaces yet. Create one above, or open one from a benchmark's chat.</p>
	{:else}
		<ul class="grid">
			{#each workspaces as ws (ws.id)}
				<li>
					<a class="card ws-card" href={`/workspace/${ws.id}`}>
						<strong>{ws.name}</strong>
						{#if ws.description}<span class="muted">{ws.description}</span>{/if}
						<span class="muted small">Updated {new Date(ws.updated_at).toLocaleString()}</span>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.page {
		width: min(100% - 2rem, 76rem);
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}
	h1 {
		margin: 0 0 0.35rem;
		font-family: var(--font-display);
	}
	.muted {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.small {
		font-size: 0.78rem;
	}
	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
		padding: 1rem 1.25rem;
	}
	button {
		padding: 0.55rem 1rem;
		border: 0;
		border-radius: 0.4rem;
		background: var(--color-accent);
		color: white;
		cursor: pointer;
	}
	.error {
		color: var(--color-danger);
		margin: 0;
	}
	.grid {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
	}
	.grid li {
		flex: 1 1 18rem;
		display: flex;
	}
	.ws-card {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		text-decoration: none;
		color: inherit;
	}
	.ws-card:hover {
		border-color: var(--color-accent-border);
	}
</style>
