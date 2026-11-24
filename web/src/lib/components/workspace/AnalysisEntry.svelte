<script lang="ts">
	import { failureMessage } from '$lib/api';
	import type { AnalysisProduct } from '$lib/workspace/products';
	import RenamableName from './RenamableName.svelte';

	interface Props {
		product: AnalysisProduct;
		onOpen: (product: AnalysisProduct) => void;
		/** Whether the stage is showing one of this analysis's files. */
		selected?: boolean;
		onOpenTurn?: (turnId: string) => void;
		onRename?: (turnId: string, title: string | null) => Promise<void>;
		/** Leaves the analysis out of the list; its files are kept. */
		onHide?: (turnId: string) => Promise<void>;
	}

	let { product, onOpen, selected = false, onOpenTurn, onRename, onHide }: Props = $props();

	let hideFailure = $state<string | null>(null);

	async function hide(turnId: string, hideIt: (turnId: string) => Promise<void>) {
		hideFailure = null;
		try {
			await hideIt(turnId);
		} catch (e) {
			hideFailure = failureMessage(e);
		}
	}

	const turnId = $derived(product.analysis.turnId);
	const fileCount = $derived(product.analysis.files.length);
	const rename = $derived(
		turnId && onRename ? (title: string | null) => onRename(turnId, title) : undefined
	);
</script>

<li class="analysis" class:selected>
	<RenamableName name={product.name} onRename={rename}>
		{#snippet children(renameButton)}
			<button
				type="button"
				class="open"
				title={product.heading.question ?? product.name}
				aria-current={selected ? 'true' : undefined}
				onclick={() => onOpen(product)}
			>
				<span class="title">{product.name}</span>
			</button>
			<p class="meta">
				<span>{fileCount} {fileCount === 1 ? 'file' : 'files'}</span>
				<span class="actions">
					{#if renameButton}{@render renameButton()}{/if}
					{#if turnId && onOpenTurn}
						<button type="button" class="link" onclick={() => onOpenTurn(turnId)}>
							Show in chat
						</button>
					{/if}
					{#if turnId && onHide}
						<button
							type="button"
							class="link"
							aria-label={`Hide ${product.name} from this list`}
							title="Hide from this list. Its files are kept."
							onclick={() => hide(turnId, onHide)}
						>
							Hide
						</button>
					{/if}
				</span>
			</p>
			{#if hideFailure}<p class="failure" role="alert">{hideFailure}</p>{/if}
		{/snippet}
	</RenamableName>
</li>

<style>
	.analysis {
		display: flex;
		flex-direction: column;
		min-width: 0;
		padding: var(--space-1) var(--space-2);
		border-radius: 0.3rem;
	}
	.analysis:hover {
		background: var(--color-surface-muted);
	}
	.selected {
		background: var(--color-accent-light);
	}
	.selected .title,
	.open:hover .title {
		color: var(--color-accent);
	}
	.open {
		display: flex;
		min-width: 0;
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: var(--text-size-label);
		font-weight: 500;
	}
	.meta {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-2);
		margin: 0;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
		white-space: nowrap;
	}
	.actions {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
	}
	.link,
	.actions :global(button.rename) {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		cursor: pointer;
	}
	.link:hover,
	.link:focus-visible {
		color: var(--color-accent);
	}
	.failure {
		margin: 0;
		font-size: var(--text-size-caption);
		color: var(--color-danger);
	}
	/* The actions are occasional, so they wait for the pointer; touch screens
	   and keyboard focus still always reach them. */
	@media (hover: hover) {
		.actions {
			visibility: hidden;
		}
		.analysis:hover .actions,
		.analysis:focus-within .actions {
			visibility: visible;
		}
	}
</style>
