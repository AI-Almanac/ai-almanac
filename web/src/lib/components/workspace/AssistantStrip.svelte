<script lang="ts">
	import { assistantStripLabel } from '$lib/workspace/zones';

	interface Props {
		/** The assistant is in the middle of a turn. */
		working?: boolean;
		/** The assistant replied while it was collapsed. */
		unread?: boolean;
		onExpand: () => void;
	}

	let { working = false, unread = false, onExpand }: Props = $props();

	const label = $derived(assistantStripLabel(working, unread));
	let button = $state<HTMLButtonElement | null>(null);

	/** For a host that closes the conversation and hands the keyboard back here. */
	export function focus() {
		button?.focus();
	}
</script>

<button
	bind:this={button}
	type="button"
	class="strip"
	aria-label={label}
	title={label}
	onclick={onExpand}
>
	<span class="icon">
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path d="M5 5h14v10H10l-5 4V5Z" />
		</svg>
		{#if working}
			<span class="mark working" aria-hidden="true"></span>
		{:else if unread}
			<span class="mark unread" aria-hidden="true"></span>
		{/if}
	</span>
	<span class="name" aria-hidden="true">Assistant</span>
</button>

<style>
	.strip {
		display: flex;
		flex: 1;
		flex-direction: column;
		align-items: center;
		gap: var(--space-2);
		min-height: 0;
		padding: var(--space-3) 0;
		border: 0;
		border-radius: inherit;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		cursor: pointer;
	}
	.strip:hover,
	.strip:focus-visible {
		background: var(--color-surface-muted);
		color: var(--color-accent);
	}
	.icon {
		position: relative;
		display: inline-flex;
	}
	svg {
		width: 1.25rem;
		height: 1.25rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.75;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.mark {
		position: absolute;
		top: -0.2rem;
		right: -0.3rem;
		width: 0.55rem;
		aspect-ratio: 1;
		border-radius: 50%;
	}
	.unread {
		background: var(--color-accent);
	}
	.working {
		background: var(--color-status-running);
		animation: pulse 1.2s ease-in-out infinite;
	}
	.name {
		font-size: var(--text-size-caption);
		font-weight: 600;
		letter-spacing: 0.05em;
		writing-mode: vertical-rl;
	}
	@keyframes pulse {
		50% {
			opacity: 0.3;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.working {
			animation: none;
		}
	}
	@media (max-width: 1050px) {
		/* The assistant is a sheet at this width, and this is the bar that opens it. */
		.strip {
			flex-direction: row;
			justify-content: center;
			min-height: var(--assistant-bar-height);
			padding: var(--space-2) var(--space-3);
			color: var(--color-text);
		}
		.name {
			writing-mode: horizontal-tb;
		}
	}
</style>
