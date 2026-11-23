<script lang="ts">
	import { ApiError } from '$lib/api/core';
	import type { SeasonWindow } from '$lib/api/workspace-settings';
	import {
		draftProblems,
		hasProblems,
		NO_PROBLEMS,
		seasonDraft,
		seasonFromDraft,
		seasonProblems,
		seasonYearProblem,
		type SeasonProblems
	} from '$lib/workspace/season-form';
	import SeasonFields from './SeasonFields.svelte';
	import SeasonYearField from './SeasonYearField.svelte';

	interface Props {
		/** The season in force: the workspace's own, or the platform default. */
		season: SeasonWindow;
		/** The year of the season the workspace works on. */
		year: number;
		/** Whether the workspace has a season of its own to go back from. */
		canReset: boolean;
		/** Saves the season and its year; a `null` season goes back to the platform dates. */
		onSave: (season: SeasonWindow | null, year: number) => Promise<void>;
		onClose: () => void;
	}

	let { season, year, canReset, onSave, onClose }: Props = $props();

	// The form edits its own copy; the season it was opened with only seeds it.
	// svelte-ignore state_referenced_locally
	let draft = $state(seasonDraft(season));
	// svelte-ignore state_referenced_locally
	let yearDraft = $state<number | null>(year);
	let yearProblem = $state<string | null>(null);
	let problems = $state<SeasonProblems>(NO_PROBLEMS);
	let saving = $state(false);

	async function save(next: SeasonWindow | null) {
		yearProblem = seasonYearProblem(yearDraft);
		if (yearProblem || yearDraft === null) return;
		saving = true;
		problems = NO_PROBLEMS;
		try {
			await onSave(next, yearDraft);
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
	<SeasonYearField bind:year={yearDraft} problem={yearProblem} />
	<SeasonFields bind:draft {problems} />

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
