<script lang="ts">
	interface Props {
		label: string;
		/** The way the zone's edge moves when pressed. */
		toward: 'left' | 'right' | 'down';
		onclick: () => void;
	}

	let { label, toward, onclick }: Props = $props();
	let button = $state<HTMLButtonElement | null>(null);

	/** For a host that hands the keyboard here after the zone opens. */
	export function focus() {
		button?.focus();
	}
</script>

<button
	bind:this={button}
	type="button"
	class="zone-toggle {toward}"
	aria-label={label}
	title={label}
	{onclick}
>
	<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5v14M20 12H9M13 8l-4 4 4 4" /></svg>
</button>

<style>
	.zone-toggle {
		display: inline-flex;
		flex: none;
		padding: var(--space-1);
		border: 0;
		border-radius: 0.3rem;
		background: none;
		color: var(--color-text-muted);
		cursor: pointer;
	}
	.zone-toggle:hover,
	.zone-toggle:focus-visible {
		background: var(--color-surface-muted);
		color: var(--color-accent);
	}
	svg {
		width: 1rem;
		height: 1rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.75;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.right svg {
		transform: scaleX(-1);
	}
	.down svg {
		transform: rotate(-90deg);
	}
</style>
