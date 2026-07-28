<script module lang="ts">
	/**
	 * Segmented control for switching between sibling views.
	 *
	 * Generalized from the `.view-toggle` in metric-map/MetricMapControls.svelte,
	 * with that component's hardcoded colors replaced by theme tokens.
	 */
	export type SegmentedTabOption = {
		value: string;
		label: string;
		/** Rendered as the button's title attribute. */
		hint?: string;
	};
</script>

<script lang="ts">
	type Props = {
		options: SegmentedTabOption[];
		value: string;
		onSelect: (value: string) => void;
		/** Accessible name for the group, e.g. "Results view". */
		ariaLabel: string;
	};

	let { options, value, onSelect, ariaLabel }: Props = $props();
</script>

<div class="segmented" role="tablist" aria-label={ariaLabel}>
	{#each options as option (option.value)}
		<button
			type="button"
			role="tab"
			aria-selected={value === option.value}
			class:active={value === option.value}
			title={option.hint}
			onclick={() => onSelect(option.value)}
		>
			{option.label}
		</button>
	{/each}
</div>

<style>
	.segmented {
		display: flex;
		gap: 0.15rem;
		padding: 0.18rem;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-surface-muted);
	}

	.segmented button {
		padding: 0.3rem 0.7rem;
		border: none;
		border-radius: 0.3rem;
		background: transparent;
		color: var(--color-text-muted);
		font-family: var(--font-body);
		font-size: 0.78rem;
		cursor: pointer;
		transition:
			background 0.12s ease,
			color 0.12s ease;
	}

	.segmented button:hover:not(.active) {
		color: var(--color-text);
	}

	.segmented button.active {
		background: var(--color-surface-raised);
		color: var(--color-accent);
		box-shadow: 0 1px 3px rgba(31, 26, 18, 0.12);
	}

	.segmented button:focus-visible {
		outline: 2px solid var(--color-accent);
		outline-offset: 1px;
	}
</style>
