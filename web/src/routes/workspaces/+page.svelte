<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { account } from '$lib/account.svelte';
	import { createWorkspace, failureMessage, listWorkspaces, type Workspace } from '$lib/api';

	$effect(() => {
		if (account.loaded && !account.canUseWorkspaces) goto('/');
	});

	let workspaces = $state<Workspace[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let name = $state('');
	let description = $state('');
	let creating = $state(false);

	onMount(async () => {
		try {
			workspaces = await listWorkspaces();
		} catch (e) {
			error = failureMessage(e);
		} finally {
			loading = false;
		}
	});

	async function create(event: SubmitEvent) {
		event.preventDefault();
		if (!name.trim()) return;
		creating = true;
		error = null;
		try {
			const created = await createWorkspace(name.trim(), description.trim());
			await goto(`/workspace/${created.id}`);
		} catch (e) {
			error = failureMessage(e);
		} finally {
			creating = false;
		}
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

	<form class="card new-form" onsubmit={create}>
		<label>
			<span>Name</span>
			<input bind:value={name} placeholder="Ethiopia Kiremt 2026" required maxlength="120" />
		</label>
		<label>
			<span>Description (optional)</span>
			<input bind:value={description} placeholder="What this project is investigating" />
		</label>
		<button type="submit" disabled={creating || !name.trim()}>
			{creating ? 'Creating…' : 'New workspace'}
		</button>
	</form>

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
	.new-form {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		align-items: flex-end;
	}
	.new-form label {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		flex: 1 1 14rem;
		font-size: 0.85rem;
		color: var(--color-text-muted);
	}
	.new-form input {
		padding: 0.5rem 0.65rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text);
	}
	button {
		padding: 0.55rem 1rem;
		border: 0;
		border-radius: 0.4rem;
		background: var(--color-accent);
		color: white;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.6;
		cursor: default;
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
