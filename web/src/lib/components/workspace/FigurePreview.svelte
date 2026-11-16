<script lang="ts">
	import { failureMessage, fetchWorkspaceArtifact, type WorkspaceArtifact } from '$lib/api';

	let { workspaceId, file }: { workspaceId: string; file: WorkspaceArtifact } = $props();

	let src = $state<string | null>(null);
	let error = $state<string | null>(null);

	$effect(() => {
		let url: string | null = null;
		let cancelled = false;
		fetchWorkspaceArtifact(workspaceId, file)
			.then((blob) => {
				if (cancelled) return;
				url = URL.createObjectURL(blob);
				src = url;
			})
			.catch((e) => (error = failureMessage(e)));
		return () => {
			cancelled = true;
			if (url) URL.revokeObjectURL(url);
		};
	});
</script>

<figure class="preview">
	{#if src}
		<a href={src} target="_blank" rel="noopener" title="Open full size">
			<img {src} alt={file.title || file.label} />
		</a>
	{:else if error}
		<p class="dim">Could not load the figure ({error}).</p>
	{:else}
		<p class="dim">Loading figure…</p>
	{/if}
</figure>

<style>
	.preview {
		display: flex;
		justify-content: center;
		margin: 0;
		padding: 0.25rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
		background: var(--color-surface-raised);
	}
	a {
		display: flex;
		cursor: zoom-in;
	}
	img {
		max-width: 100%;
		height: auto;
	}
	.dim {
		margin: 1.5rem 0;
		color: var(--color-text-dim);
		font-size: 0.85rem;
	}
</style>
