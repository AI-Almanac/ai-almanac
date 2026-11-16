<script lang="ts">
	import { tick } from 'svelte';

	import { menuMove, type NewRunEntry } from '$lib/workspace/new-run-menu';
	import type { PanelName } from '$lib/workspace/panels';

	interface Props {
		entries: NewRunEntry[];
		onChoose: (panel: PanelName) => void;
		/** Just the plus sign, for the collapsed products strip. */
		compact?: boolean;
	}

	let { entries, onChoose, compact = false }: Props = $props();

	let open = $state(false);
	let root = $state<HTMLElement | null>(null);
	let trigger = $state<HTMLButtonElement | null>(null);

	function items(): HTMLElement[] {
		return [...(root?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
	}

	async function openMenu() {
		open = true;
		await tick();
		items()[0]?.focus();
	}

	function closeMenu(returnFocus: boolean) {
		open = false;
		if (returnFocus) trigger?.focus();
	}

	// Focus goes back to the trigger first, so whatever the choice opens knows what opened it.
	function choose(panel: PanelName) {
		closeMenu(true);
		onChoose(panel);
	}

	function closeFromOutside(event: MouseEvent) {
		if (open && root && !root.contains(event.target as Node)) closeMenu(false);
	}

	function moveWithin(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			closeMenu(true);
			return;
		}
		// Tab leaves the menu, which closes behind it.
		if (event.key === 'Tab') return closeMenu(false);
		const all = items();
		const next = menuMove(
			event.key,
			all.indexOf(document.activeElement as HTMLElement),
			all.length
		);
		if (next === null) return;
		event.preventDefault();
		all[next].focus();
	}

	function openFromKeyboard(event: KeyboardEvent) {
		if (open || event.key !== 'ArrowDown') return;
		event.preventDefault();
		void openMenu();
	}
</script>

<svelte:window onclick={closeFromOutside} />

<div class="new-run" class:compact bind:this={root}>
	<button
		bind:this={trigger}
		type="button"
		class="trigger"
		aria-haspopup="menu"
		aria-expanded={open}
		aria-label={compact ? 'New' : undefined}
		title={compact ? 'New benchmark, blend, forecast or existing run' : undefined}
		onclick={() => (open ? closeMenu(false) : openMenu())}
		onkeydown={openFromKeyboard}
	>
		<span aria-hidden="true">+</span>{#if !compact}&nbsp;New{/if}
	</button>
	{#if open}
		<ul class="menu" role="menu" aria-label="New" onkeydown={moveWithin}>
			{#each entries as entry (entry.key)}
				<li role="none">
					<button
						type="button"
						role="menuitem"
						tabindex="-1"
						class="entry"
						onclick={() => choose(entry.panel)}
					>
						<span>{entry.label}</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.new-run {
		position: relative;
		display: flex;
	}
	.trigger {
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--color-accent-border);
		border-radius: 0.35rem;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
	}
	.compact .trigger {
		padding: var(--space-1) var(--space-2);
	}
	.menu {
		position: absolute;
		top: calc(100% + var(--space-1));
		left: 0;
		z-index: 30;
		display: flex;
		flex-direction: column;
		width: max-content;
		max-width: min(18rem, 80vw);
		margin: 0;
		padding: var(--space-1);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface-raised);
		box-shadow: var(--shadow-soft);
		list-style: none;
	}
	.entry {
		display: flex;
		flex-direction: column;
		gap: 0;
		width: 100%;
		padding: var(--space-2) var(--space-3);
		border: 0;
		border-radius: 0.35rem;
		background: none;
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-body);
		text-align: left;
		text-decoration: none;
		cursor: pointer;
	}
	.entry:hover,
	.entry:focus-visible {
		background: var(--color-accent-light);
	}
	.entry:focus-visible {
		outline: 0.125rem solid var(--color-accent);
		outline-offset: -0.125rem;
	}
	@media (min-width: 1051px) {
		/* Beside the strip rather than under it, where the stage has the room. */
		.compact .menu {
			top: 0;
			left: calc(100% + var(--space-1));
		}
	}
</style>
