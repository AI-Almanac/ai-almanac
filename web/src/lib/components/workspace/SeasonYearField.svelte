<script lang="ts">
	import { SEASON_YEARS } from '$lib/workspace/season-form';

	interface Props {
		/** The year of the season the workspace works on; null while the field is empty. */
		year: number | null;
		problem?: string | null;
	}

	let { year = $bindable(), problem = null }: Props = $props();

	const id = $props.id();
</script>

<!-- Which year's season the workspace works on: the user's choice, never moved on for them. -->
<label class="field">
	<span class="label">Season year</span>
	<input
		type="number"
		inputmode="numeric"
		min={SEASON_YEARS.min}
		max={SEASON_YEARS.max}
		step="1"
		bind:value={year}
		aria-invalid={Boolean(problem)}
		aria-describedby={problem ? `${id}-problem` : undefined}
	/>
	{#if problem}<span class="problem" id="{id}-problem">{problem}</span>{/if}
</label>

<style>
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-width: 0;
	}
	.label {
		font-size: var(--text-size-label);
		font-weight: 600;
		color: var(--color-text);
	}
	input {
		width: 8ch;
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface-raised);
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-body);
	}
	input[aria-invalid='true'] {
		border-color: var(--color-danger);
	}
	.problem {
		color: var(--color-danger);
		font-size: var(--text-size-label);
	}
</style>
