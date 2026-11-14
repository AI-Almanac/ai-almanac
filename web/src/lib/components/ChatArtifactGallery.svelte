<script lang="ts">
	import FigureCard from '$lib/components/FigureCard.svelte';
	import { copyCode, type GalleryFigure } from '$lib/chat/format';
	import { toolLabel } from '$lib/chat/tools';

	interface Props {
		figures: GalleryFigure[];
		onOpenFigure: (index: number) => void;
	}

	const { figures, onOpenFigure }: Props = $props();

	let shownCode = $state<Set<string>>(new Set());

	function toggleCode(key: string) {
		const next = new Set(shownCode);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		shownCode = next;
	}
</script>

<section class="artifact-gallery artifact-gallery-tab">
	{#if figures.length > 0}
		<div class="artifact-gallery-header">
			<span class="artifact-gallery-title">Artifacts</span>
			<span class="artifact-gallery-count">{figures.length}</span>
		</div>
		<div class="artifact-gallery-grid">
			{#each figures as item, fi (item.artifactId)}
				{@const codeKey = `artifact-${item.artifactId}`}
				<div class="artifact-item">
					<FigureCard figure={item.figure} onclick={() => onOpenFigure(fi)} />
					<div class="artifact-meta">
						<div class="artifact-meta-row">
							<span class="artifact-source">
								{item.toolName ? toolLabel({ name: item.toolName }, 'done') : 'Generated figure'}
							</span>
							<span class="artifact-time"
								>{new Date(item.createdAt).toLocaleTimeString([], {
									hour: 'numeric',
									minute: '2-digit'
								})}</span
							>
						</div>
						{#if item.code}
							<div class="artifact-actions">
								<button class="code-action-btn" onclick={() => copyCode(item.code!)}
									>Copy code</button
								>
								<button class="code-action-btn" onclick={() => toggleCode(codeKey)}>
									{shownCode.has(codeKey) ? 'Hide code' : 'Show code'}
								</button>
							</div>
							{#if shownCode.has(codeKey)}
								<pre class="code-block artifact-code"><code>{item.code}</code></pre>
							{/if}
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{:else}
		<div class="artifact-gallery-empty">Generated figures will appear here.</div>
	{/if}
</section>

<style>
	.artifact-gallery {
		border: 1px solid var(--color-border);
		border-radius: 0.6rem;
		background: linear-gradient(180deg, var(--color-surface) 0%, var(--color-surface-raised) 100%);
		overflow: hidden;
	}

	.artifact-gallery-tab {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-height: 0;
		margin: 0.85rem;
	}

	.artifact-gallery-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		padding: 0.7rem 0.9rem;
		border-bottom: 1px solid var(--color-border-subtle);
		background: rgba(212, 147, 63, 0.05);
	}

	.artifact-gallery-title {
		font-size: var(--text-size-caption);
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--color-accent);
	}

	.artifact-gallery-count {
		min-width: 1.5rem;
		padding: 0.1rem 0.45rem;
		border-radius: 999px;
		background: var(--color-surface);
		border: 1px solid var(--color-border-subtle);
		color: var(--color-text-muted);
		font-size: var(--text-size-caption);
		text-align: center;
	}

	.artifact-gallery-grid {
		display: grid;
		gap: var(--space-3);
		padding: 0.85rem;
		overflow-y: auto;
	}

	.artifact-item {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
	}

	.artifact-meta {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		padding: 0 0.1rem;
	}

	.artifact-meta-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
	}

	.artifact-source {
		font-size: var(--text-size-caption);
		font-weight: 600;
		color: var(--color-accent);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.artifact-time {
		font-size: var(--text-size-caption);
		color: var(--color-text-dim);
	}

	.artifact-actions {
		display: flex;
		gap: 0.35rem;
	}

	.code-action-btn {
		padding: 0.15rem var(--space-2);
		background: none;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		font-family: inherit;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
		cursor: pointer;
		transition:
			color 0.12s,
			border-color 0.12s;
	}
	.code-action-btn:hover {
		color: var(--color-accent);
		border-color: var(--color-accent);
	}

	.code-block {
		margin: 0;
		padding: var(--space-3) var(--space-4);
		background: var(--color-bg);
		border-top: 1px solid var(--color-border);
		overflow-x: auto;
		font-family: var(--font-mono, monospace);
		font-size: var(--text-size-label);
		line-height: 1.5;
		color: var(--color-text);
		white-space: pre;
	}

	.artifact-code {
		font-size: var(--text-size-caption);
	}

	.artifact-gallery-empty {
		padding: var(--space-4);
		color: var(--color-text-muted);
		font-size: var(--text-size-body);
	}
</style>
