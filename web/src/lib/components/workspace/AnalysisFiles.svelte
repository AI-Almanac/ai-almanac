<script lang="ts">
	import type { WorkspaceArtifact } from '$lib/api';
	import type { Analysis } from '$lib/workspace/analyses';
	import {
		analysisFileName,
		offersResultChoice,
		sortAnalysisFiles
	} from '$lib/workspace/supporting';

	interface Props {
		analysis: Analysis;
		/** The file the stage is showing. */
		shownId: string;
		onOpen: (file: WorkspaceArtifact) => void;
	}

	let { analysis, shownId, onOpen }: Props = $props();

	const sorted = $derived(sortAnalysisFiles(analysis.files));
	const results = $derived(offersResultChoice(sorted.results, shownId) ? sorted.results : []);
	const showingScript = $derived(sorted.scripts.some((file) => file.id === shownId));
	const hasAny = $derived(results.length + sorted.supporting.length + sorted.scripts.length > 0);

	let codeOpen = $state(false);

	function openScript(file: WorkspaceArtifact) {
		codeOpen = false;
		onOpen(file);
	}

	function closeWhenFocusLeaves(event: FocusEvent & { currentTarget: HTMLElement }) {
		if (!event.currentTarget.contains(event.relatedTarget as Node | null)) codeOpen = false;
	}
</script>

{#snippet fileButton(file: WorkspaceArtifact, supporting: boolean)}
	<li>
		<button
			type="button"
			class="file"
			class:supporting
			aria-current={file.id === shownId ? 'true' : undefined}
			title={supporting ? 'A file this analysis was made from' : file.title || file.label}
			onclick={() => onOpen(file)}
		>
			{file.title || file.label}
		</button>
	</li>
{/snippet}

<!-- An analysis can make several files; the stage shows one, so the rest are a press away. -->
{#if hasAny}
	<nav class="analysis-files" aria-label="Files in this analysis">
		<ul>
			{#each results as file (file.id)}
				{@render fileButton(file, false)}
			{/each}
			{#each sorted.supporting as file (file.id)}
				{@render fileButton(file, true)}
			{/each}
			{#if sorted.scripts.length > 0}
				<li class="code">
					<details bind:open={codeOpen} onfocusout={closeWhenFocusLeaves}>
						<summary class="file supporting" class:current={showingScript}>
							Code ({sorted.scripts.length})
						</summary>
						<ul class="scripts">
							{#each sorted.scripts as file (file.id)}
								{@const name = analysisFileName(file, analysis.files)}
								<li>
									<button
										type="button"
										class="script"
										aria-current={file.id === shownId ? 'true' : undefined}
										title={name}
										onclick={() => openScript(file)}
									>
										{name}
									</button>
								</li>
							{/each}
						</ul>
					</details>
				</li>
			{/if}
		</ul>
	</nav>
{/if}

<style>
	ul {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.file {
		display: block;
		max-width: 16rem;
		overflow: hidden;
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.35rem;
		background: none;
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-label);
		text-overflow: ellipsis;
		white-space: nowrap;
		cursor: pointer;
	}
	.file:hover,
	.file:focus-visible {
		border-color: var(--color-accent-border);
		color: var(--color-accent);
	}
	.file[aria-current='true'],
	.file.current {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
	}
	.supporting {
		color: var(--color-text-muted);
	}
	.code {
		position: relative;
	}
	summary {
		list-style: none;
	}
	summary::-webkit-details-marker {
		display: none;
	}
	summary::after {
		content: ' ▾';
	}
	/* A menu over the stage, so opening it does not push the file down. */
	.scripts {
		position: absolute;
		top: calc(100% + var(--space-1));
		left: 0;
		z-index: 2;
		flex-direction: column;
		flex-wrap: nowrap;
		gap: 0;
		min-width: 14rem;
		max-width: min(28rem, 80vw);
		padding: var(--space-1);
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface);
		box-shadow: 0 0.25rem 0.75rem rgb(0 0 0 / 0.12);
	}
	.script {
		width: 100%;
		overflow: hidden;
		padding: var(--space-1) var(--space-2);
		border: 0;
		border-radius: 0.25rem;
		background: none;
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-label);
		text-align: left;
		text-overflow: ellipsis;
		white-space: nowrap;
		cursor: pointer;
	}
	.script:hover,
	.script:focus-visible {
		background: var(--color-surface-muted);
		color: var(--color-accent);
	}
	.script[aria-current='true'] {
		color: var(--color-accent);
		font-weight: 600;
	}
</style>
