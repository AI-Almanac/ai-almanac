<script lang="ts">
	import { ApiError } from '$lib/api/core';
	import type { SeasonWindow } from '$lib/api/workspace-settings';
	import {
		daysIn,
		draftProblems,
		hasProblems,
		NO_PROBLEMS,
		SEASON_DATE_FIELDS,
		seasonDraft,
		seasonFromDraft,
		seasonProblems,
		type SeasonProblems
	} from '$lib/workspace/season-form';
	import { MONTH_NAMES } from '$lib/format/dates';

	interface Props {
		/** The season in force: the workspace's own, or the platform default. */
		season: SeasonWindow;
		/** Whether the workspace has a season of its own to go back from. */
		canReset: boolean;
		/** Saves the season; `null` goes back to the platform default. */
		onSave: (season: SeasonWindow | null) => Promise<void>;
		onClose: () => void;
	}

	let { season, canReset, onSave, onClose }: Props = $props();

	// The form edits its own copy; the season it was opened with only seeds it.
	// svelte-ignore state_referenced_locally
	let draft = $state(seasonDraft(season));
	let problems = $state<SeasonProblems>(NO_PROBLEMS);
	let saving = $state(false);

	async function save(next: SeasonWindow | null) {
		saving = true;
		problems = NO_PROBLEMS;
		try {
			await onSave(next);
			onClose();
		} catch (e) {
			problems = seasonProblems(e instanceof ApiError ? e.detail : String(e));
		} finally {
			saving = false;
		}
	}

	function submit(event: SubmitEvent) {
		event.preventDefault();
		const found = draftProblems(draft);
		problems = found;
		if (!hasProblems(found)) void save(seasonFromDraft(draft));
	}
</script>

<form class="season-form" onsubmit={submit} novalidate>
	<label class="field">
		<span class="label">Season name</span>
		<input
			type="text"
			bind:value={draft.name}
			aria-invalid={Boolean(problems.fields.name)}
			aria-describedby={problems.fields.name ? 'season-name-problem' : undefined}
		/>
		{#if problems.fields.name}
			<span class="problem" id="season-name-problem">{problems.fields.name}</span>
		{/if}
	</label>

	{#each SEASON_DATE_FIELDS as { field, label } (field)}
		{@const problem = problems.fields[field]}
		<fieldset class="field" aria-describedby={problem ? `season-${field}-problem` : undefined}>
			<legend class="label">{label}</legend>
			<div class="date">
				<select aria-label="{label}: day" bind:value={draft[field].day}>
					{#each daysIn(draft[field].month) as day (day)}
						<option value={day}>{day}</option>
					{/each}
				</select>
				<select aria-label="{label}: month" bind:value={draft[field].month}>
					{#each MONTH_NAMES as name, index (name)}
						<option value={index + 1}>{name}</option>
					{/each}
				</select>
			</div>
			{#if problem}<span class="problem" id="season-{field}-problem">{problem}</span>{/if}
		</fieldset>
	{/each}

	{#if problems.general}<p class="problem" role="alert">{problems.general}</p>{/if}

	<div class="actions">
		<button type="submit" class="save" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
		<button type="button" class="quiet" onclick={onClose}>Cancel</button>
		{#if canReset}
			<button type="button" class="quiet reset" disabled={saving} onclick={() => save(null)}>
				Use the default dates
			</button>
		{/if}
	</div>
	<p class="note">Runs already made keep the season they were made with.</p>
</form>

<style>
	.season-form {
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
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-3);
	}
	.save {
		padding: var(--space-1) var(--space-4);
		border: 0;
		border-radius: 0.35rem;
		background: var(--color-accent);
		color: white;
		font: inherit;
		font-size: var(--text-size-label);
		font-weight: 600;
		cursor: pointer;
	}
	.quiet {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: var(--text-size-label);
		cursor: pointer;
	}
	.reset {
		margin-left: auto;
	}
	.note {
		margin: 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-caption);
	}
</style>
