<script lang="ts">
	import { failureMessage, type WorkspaceSummary } from '$lib/api';
	import type { FocusAreaValue } from '$lib/api/jobs';
	import type { SeasonInForce, SeasonSave } from '$lib/workspace/season-form';
	import { seasonFact } from '$lib/workspace/summary';
	import AreaControl from './AreaControl.svelte';
	import SeasonControl from './SeasonControl.svelte';

	interface Props {
		/** Still under the name a blank workspace is made with. */
		untitled: boolean;
		onName: (name: string) => Promise<void>;
		area: FocusAreaValue | null;
		saveArea: (area: FocusAreaValue | null) => Promise<void>;
		/** The season in force; without it the season is left to the header. */
		summary: WorkspaceSummary | null;
		loadSeason: () => Promise<SeasonInForce>;
		saveSeason: (change: SeasonSave) => Promise<void>;
	}

	let { untitled, onName, area, saveArea, summary, loadSeason, saveSeason }: Props = $props();

	let name = $state('');
	let naming = $state(false);
	let problem = $state<string | null>(null);

	const season = $derived(summary ? seasonFact(summary.season) : null);

	async function keepName(event: SubmitEvent) {
		event.preventDefault();
		if (!name.trim()) return;
		naming = true;
		problem = null;
		try {
			await onName(name.trim());
		} catch (e) {
			problem = failureMessage(e);
		} finally {
			naming = false;
		}
	}
</script>

<!-- What a workspace is about, asked for once while it is still empty; each can be changed later from the header. -->
<section class="setup" aria-labelledby="workspace-setup-heading">
	<h2 id="workspace-setup-heading">Set up this workspace</h2>
	<ol class="steps">
		<li class:done={!untitled}>
			<span class="mark" aria-hidden="true">{untitled ? '1' : '✓'}</span>
			{#if untitled}
				<form class="name" onsubmit={keepName}>
					<label for="workspace-setup-name">Name it</label>
					<input
						id="workspace-setup-name"
						bind:value={name}
						maxlength="120"
						placeholder="e.g. Ethiopia Kiremt 2026"
					/>
					<button type="submit" disabled={naming || !name.trim()}>
						{naming ? 'Saving…' : 'Save'}
					</button>
				</form>
			{:else}
				<span>Named</span>
			{/if}
		</li>
		<li class:done={area !== null}>
			<span class="mark" aria-hidden="true">{area ? '✓' : '2'}</span>
			<span class="step">
				<span>Choose where runs are scored</span>
				<AreaControl {area} save={saveArea} />
			</span>
		</li>
		{#if summary && season}
			<li class:done={!summary.season.is_default}>
				<span class="mark" aria-hidden="true">{summary.season.is_default ? '3' : '✓'}</span>
				<span class="step">
					<span>Check the season</span>
					<SeasonControl
						headline={season.headline}
						detail={season.detail}
						isDefault={summary.season.is_default}
						load={loadSeason}
						save={saveSeason}
					/>
				</span>
			</li>
		{/if}
	</ol>
	{#if problem}<p class="problem" role="alert">{problem}</p>{/if}
</section>

<style>
	.setup {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
	h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-title);
	}
	.steps {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.steps li {
		display: flex;
		align-items: baseline;
		gap: var(--space-3);
		font-size: var(--text-size-body);
	}
	.mark {
		display: inline-flex;
		flex: none;
		align-items: center;
		justify-content: center;
		width: 1.5em;
		height: 1.5em;
		border: 1px solid var(--color-border);
		border-radius: 50%;
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
	.done .mark {
		border-color: var(--color-accent);
		background: var(--color-accent);
		color: white;
	}
	.step,
	.name {
		display: flex;
		flex: 1;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-2) var(--space-3);
		min-width: 0;
	}
	.name input {
		flex: 1 1 12rem;
		min-width: 0;
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	.name button {
		padding: var(--space-1) var(--space-3);
		border: 0;
		border-radius: 0.4rem;
		background: var(--color-accent);
		color: white;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	.name button:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.problem {
		margin: 0;
		color: var(--color-danger);
		font-size: var(--text-size-label);
	}
</style>
