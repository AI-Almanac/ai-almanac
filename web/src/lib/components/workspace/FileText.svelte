<script lang="ts">
	import type { Snippet } from 'svelte';
	import { failureMessage, fetchWorkspaceArtifact } from '$lib/api';

	interface Props {
		workspaceId: string;
		fileId: string;
		children: Snippet<[string]>;
	}

	let { workspaceId, fileId, children }: Props = $props();

	let text = $state<string | null>(null);
	let error = $state<string | null>(null);

	$effect(() => {
		let cancelled = false;
		text = null;
		error = null;
		fetchWorkspaceArtifact(workspaceId, { id: fileId })
			.then((blob) => blob.text())
			.then((loaded) => {
				if (!cancelled) text = loaded;
			})
			.catch((e) => {
				if (!cancelled) error = failureMessage(e);
			});
		return () => {
			cancelled = true;
		};
	});
</script>

{#if text !== null}
	{@render children(text)}
{:else if error}
	<p class="dim">Could not load this file ({error}).</p>
{:else}
	<p class="dim">Loading…</p>
{/if}

<style>
	.dim {
		margin: 0;
		color: var(--color-text-dim);
		font-size: 0.84rem;
	}
</style>
