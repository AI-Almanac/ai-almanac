<script lang="ts">
	import { failureMessage } from '$lib/api';
	import type { FocusAreaValue } from '$lib/api/jobs';
	import type { SeasonWindow, WorkspaceSettings } from '$lib/api/workspace-settings';
	import FocusAreaMap from '$lib/components/FocusAreaMap.svelte';
	import { chosenSettings, countryOfArea } from '$lib/workspace/new-workspace';
	import {
		draftProblems,
		hasProblems,
		NO_PROBLEMS,
		seasonDraft,
		seasonFromDraft,
		seasonLabel,
		seasonYearProblem,
		type SeasonDraft,
		type SeasonProblems
	} from '$lib/workspace/season-form';
	import SeasonFields from './SeasonFields.svelte';
	import SeasonYearField from './SeasonYearField.svelte';

	interface Props {
		/** The season a workspace in `country` works in unless it sets its own. */
		usualSeason: (country: string | null) => Promise<SeasonWindow>;
		onCreate: (name: string, description: string, settings?: WorkspaceSettings) => Promise<void>;
		onCancel: () => void;
	}

	let { usualSeason, onCreate, onCancel }: Props = $props();

	let name = $state('');
	let description = $state('');
	let area = $state<FocusAreaValue | null>(null);
	let seasonChoice = $state<'usual' | 'own'>('usual');
	let usual = $state<SeasonWindow | null>(null);
	let draft = $state<SeasonDraft | null>(null);
	let seasonYear = $state<number | null>(new Date().getFullYear());
	let yearProblem = $state<string | null>(null);
	let problems = $state<SeasonProblems>(NO_PROBLEMS);
	let creating = $state(false);
	let error = $state<string | null>(null);

	const country = $derived(countryOfArea(area));

	// The usual season follows the area's country; an answer for an earlier country is dropped.
	$effect(() => {
		const asked = country;
		let current = true;
		usual = null;
		usualSeason(asked)
			.then((season) => current && (usual = season))
			.catch(() => current && (usual = null));
		return () => {
			current = false;
		};
	});

	function chooseOwnDates() {
		if (usual) draft ??= seasonDraft(usual);
		seasonChoice = 'own';
	}

	function seasonToKeep(): SeasonWindow | null | 'invalid' {
		if (seasonChoice === 'usual' || !draft) return null;
		problems = draftProblems(draft);
		return hasProblems(problems) ? 'invalid' : seasonFromDraft(draft);
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		const season = seasonToKeep();
		yearProblem = seasonYearProblem(seasonYear);
		if (!name.trim() || season === 'invalid' || yearProblem || seasonYear === null) return;
		creating = true;
		error = null;
		try {
			await onCreate(name.trim(), description.trim(), chosenSettings(area, season, seasonYear));
		} catch (e) {
			error = failureMessage(e);
		} finally {
			creating = false;
		}
	}
</script>

<form class="new-workspace" onsubmit={submit} novalidate aria-labelledby="new-workspace-heading">
	<h2 id="new-workspace-heading">New workspace</h2>

	<div class="row">
		<label class="field">
			<span class="label">Name</span>
			<input bind:value={name} placeholder="Ethiopia Kiremt 2026" required maxlength="120" />
		</label>
		<label class="field">
			<span class="label">Description (optional)</span>
			<input bind:value={description} placeholder="What this project is investigating" />
		</label>
	</div>

	<fieldset class="field">
		<legend class="label">Area (optional)</legend>
		<p class="hint">Benchmarks and blends here are scored in this area unless one names its own.</p>
		<FocusAreaMap value={area} onchange={(next) => (area = next)} />
	</fieldset>

	<fieldset class="field">
		<legend class="label">Season</legend>
		<SeasonYearField bind:year={seasonYear} problem={yearProblem} />
		<label class="choice">
			<input
				type="radio"
				name="season-choice"
				checked={seasonChoice === 'usual'}
				onchange={() => (seasonChoice = 'usual')}
			/>
			<span>
				The usual season here
				<span class="hint">{usual ? seasonLabel(usual) : 'Looking it up…'}</span>
			</span>
		</label>
		<label class="choice">
			<input
				type="radio"
				name="season-choice"
				checked={seasonChoice === 'own'}
				disabled={!usual && !draft}
				onchange={chooseOwnDates}
			/>
			<span>Set my own dates</span>
		</label>
		{#if seasonChoice === 'own' && draft}
			<div class="own-dates"><SeasonFields bind:draft {problems} /></div>
		{/if}
	</fieldset>

	<p class="hint">You can change the area and season later from the top of the workspace.</p>
	{#if error}<p class="problem" role="alert">{error}</p>{/if}

	<div class="actions">
		<button type="submit" class="create" disabled={creating || !name.trim()}>
			{creating ? 'Creating…' : 'Create workspace'}
		</button>
		<button type="button" class="quiet" onclick={onCancel}>Cancel</button>
	</div>
</form>

<style>
	.new-workspace {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}
	h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-title);
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
	}
	.row .field {
		flex: 1 1 14rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
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
	.field > input {
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	.choice {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		font-size: var(--text-size-body);
	}
	.choice > span {
		display: flex;
		flex-direction: column;
	}
	.own-dates {
		padding-left: var(--space-5);
	}
	.hint {
		margin: 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
	.problem {
		margin: 0;
		color: var(--color-danger);
		font-size: var(--text-size-label);
	}
	.actions {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}
	.create {
		padding: var(--space-2) var(--space-4);
		border: 0;
		border-radius: 0.4rem;
		background: var(--color-accent);
		color: white;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	.create:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.quiet {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		cursor: pointer;
	}
</style>
