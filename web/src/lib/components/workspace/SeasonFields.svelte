<script lang="ts">
	import { MONTH_NAMES } from '$lib/format/dates';
	import {
		daysIn,
		inMonth,
		SEASON_DATE_FIELDS,
		type SeasonDraft,
		type SeasonProblems
	} from '$lib/workspace/season-form';

	interface Props {
		/** The season being edited; changed in place as the fields change. */
		draft: SeasonDraft;
		problems: SeasonProblems;
	}

	let { draft = $bindable(), problems }: Props = $props();

	const id = $props.id();
</script>

<!-- A season's name and dates, for whichever form is choosing them. -->
<div class="season-fields">
	<label class="field">
		<span class="label">Season name</span>
		<input
			type="text"
			bind:value={draft.name}
			aria-invalid={Boolean(problems.fields.name)}
			aria-describedby={problems.fields.name ? `${id}-name-problem` : undefined}
		/>
		{#if problems.fields.name}
			<span class="problem" id="{id}-name-problem">{problems.fields.name}</span>
		{/if}
	</label>

	{#each SEASON_DATE_FIELDS as { field, label } (field)}
		{@const problem = problems.fields[field]}
		<fieldset class="field" aria-describedby={problem ? `${id}-${field}-problem` : undefined}>
			<legend class="label">{label}</legend>
			<div class="date">
				<select aria-label="{label}: day" bind:value={draft[field].day}>
					{#each daysIn(draft[field].month) as day (day)}
						<option value={day}>{day}</option>
					{/each}
				</select>
				<select
					aria-label="{label}: month"
					value={draft[field].month}
					onchange={(event) =>
						(draft[field] = inMonth(draft[field], Number(event.currentTarget.value)))}
				>
					{#each MONTH_NAMES as name, index (name)}
						<option value={index + 1}>{name}</option>
					{/each}
				</select>
			</div>
			{#if problem}<span class="problem" id="{id}-{field}-problem">{problem}</span>{/if}
		</fieldset>
	{/each}
</div>

<style>
	.season-fields {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-width: 0;
		margin: 0;
		padding: 0;
		border: 0;
	}
	.label {
		padding: 0;
		font-size: var(--text-size-label);
		font-weight: 600;
		color: var(--color-text);
	}
	.date {
		display: flex;
		gap: var(--space-2);
	}
	.date select:last-child {
		flex: 1;
	}
	input,
	select {
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
		margin: 0;
		color: var(--color-danger);
		font-size: var(--text-size-label);
	}
</style>
