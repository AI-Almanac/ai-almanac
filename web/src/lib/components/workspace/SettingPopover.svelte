<script lang="ts">
	import type { Snippet } from 'svelte';
	import { overlay } from '$lib/workspace/overlay';

	interface Props {
		/** What the setting is, e.g. "Season"; it names the popover. */
		label: string;
		/** The trigger's tooltip: what pressing it changes. */
		title: string;
		/** Room for a map rather than a short form. */
		wide?: boolean;
		/** Each time the popover opens, so the editor can start afresh. */
		onOpen?: () => void;
		/** The setting as it stands, on the trigger. */
		value: Snippet;
		/** The editor, handed a way to close the popover. */
		children: Snippet<[close: () => void]>;
	}

	let { label, title, wide = false, onOpen, value, children }: Props = $props();

	let open = $state(false);
	let opener = $state<HTMLButtonElement | null>(null);

	function close() {
		open = false;
	}

	function toggle() {
		open = !open;
		if (open) onOpen?.();
	}
</script>

<!-- A workspace setting in the header: the value as it stands, which opens its editor. -->
<span class="setting">
	<button
		bind:this={opener}
		type="button"
		class="trigger"
		aria-haspopup="dialog"
		aria-expanded={open}
		{title}
		onclick={toggle}
	>
		{@render value()}
		<span class="edit" aria-hidden="true">✎</span>
	</button>
	{#if open}
		<div
			class="popover"
			class:wide
			role="dialog"
			aria-label={label}
			use:overlay={{ onClose: close, opener, closeOnOutsideClick: true }}
		>
			{@render children(close)}
		</div>
	{/if}
</span>

<style>
	.setting {
		position: relative;
		display: inline-flex;
	}
	.trigger {
		display: inline-flex;
		align-items: baseline;
		gap: var(--space-1);
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}
	.trigger:hover,
	.trigger:focus-visible {
		text-decoration: underline;
	}
	.edit {
		color: var(--color-accent);
	}
	.popover {
		position: absolute;
		top: calc(100% + var(--space-2));
		left: 0;
		z-index: 30;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		width: min(22rem, 90vw);
		padding: var(--space-4);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface-raised);
		box-shadow: var(--shadow-soft);
		white-space: normal;
	}
	.popover.wide {
		width: min(40rem, 90vw);
	}
</style>
