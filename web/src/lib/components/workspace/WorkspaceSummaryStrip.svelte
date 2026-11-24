<script lang="ts">
	import type { WorkspaceSummary } from '$lib/api';
	import type { FocusAreaValue } from '$lib/api/jobs';
	import type { SeasonInForce, SeasonSave } from '$lib/workspace/season-form';
	import { studyAreaWords } from '$lib/workspace/study-area';
	import { seasonFact, setupChips } from '$lib/workspace/summary';
	import AreaControl from './AreaControl.svelte';
	import SeasonControl from './SeasonControl.svelte';

	const DEFAULT_SEASON_NOTE = 'Platform default dates; no season has been set for this workspace';

	interface Props {
		summary: WorkspaceSummary;
		/** Makes the season editable: reads the season in force, and saves a change. */
		seasonEditor?: {
			load: () => Promise<SeasonInForce>;
			save: (change: SeasonSave) => Promise<void>;
		};
		/** The workspace's area and how to change it; without it the area is not shown. */
		areaEditor?: {
			area: FocusAreaValue | null;
			save: (area: FocusAreaValue | null) => Promise<void>;
		};
	}

	let { summary, seasonEditor, areaEditor }: Props = $props();

	const season = $derived(seasonFact(summary.season));
	const areaWords = $derived(areaEditor ? studyAreaWords(areaEditor.area) : null);
	const setup = $derived(setupChips(summary.setup, areaWords).join(' · '));
</script>

<!-- One line of what the workspace is about; what each fact rests on is a hover away. -->
<div class="summary" role="group" aria-label="Workspace summary">
	{#if areaEditor}
		<span class="item">
			<AreaControl area={areaEditor.area} save={areaEditor.save} />
		</span>
	{/if}
	{#if seasonEditor}
		<span class="item">
			<SeasonControl
				headline={season.headline}
				detail={season.detail}
				isDefault={summary.season.is_default}
				load={seasonEditor.load}
				save={seasonEditor.save}
			/>
		</span>
	{:else}
		<span class="item" title={season.detail ?? undefined}>
			{season.headline}
			{#if summary.season.is_default}
				<span class="dim" title={DEFAULT_SEASON_NOTE}>(default dates)</span>
			{/if}
		</span>
	{/if}
	{#if setup}
		<span class="item dim" title="What this workspace is set up for">{setup}</span>
	{/if}
</div>

<style>
	.summary {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-1) 0;
		margin: 0;
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
		background: var(--color-surface-muted);
		font-size: var(--text-size-label);
		line-height: 1.4;
		color: var(--color-text);
	}
	.item {
		display: inline-flex;
		align-items: baseline;
		gap: var(--space-1);
		min-width: 0;
		white-space: nowrap;
	}
	/* A hairline between facts, dropped where a wrap puts one first on its line. */
	.item + .item::before {
		content: '';
		align-self: center;
		width: 1px;
		height: 0.9em;
		margin: 0 var(--space-3);
		background: var(--color-border);
	}
	.dim {
		color: var(--color-text-muted);
	}
</style>
