<script lang="ts">
	import type { Snippet } from 'svelte';
	import NameField from './NameField.svelte';

	interface Props {
		/** The name shown now, which renaming starts from. */
		name: string;
		/** Saves a new name; `null` asks for the default back. Without it nothing is renamed. */
		onRename?: (title: string | null) => Promise<void>;
		/** What shows while not renaming, handed the Rename button to place, or null without one. */
		children: Snippet<[rename: Snippet | null]>;
	}

	let { name, onRename, children }: Props = $props();

	let renaming = $state(false);
</script>

{#snippet renameButton()}
	<button
		type="button"
		class="rename"
		aria-label={`Rename ${name}`}
		onclick={() => (renaming = true)}
	>
		Rename
	</button>
{/snippet}

{#if renaming && onRename}
	<NameField value={name} onSave={onRename} onClose={() => (renaming = false)} />
{:else}
	{@render children(onRename ? renameButton : null)}
{/if}

<style>
	.rename {
		flex: none;
		padding: 0.15rem 0.3rem;
		border: 0;
		border-radius: 0.25rem;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		font-size: var(--text-size-caption);
		cursor: pointer;
	}
	.rename:hover,
	.rename:focus-visible {
		color: var(--color-accent);
	}
</style>
