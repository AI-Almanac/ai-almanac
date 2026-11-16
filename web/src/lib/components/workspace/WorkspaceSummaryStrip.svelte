<script lang="ts">
	import type { WorkspaceSummary } from '$lib/api';
	import type { FocusAreaValue } from '$lib/api/jobs';
	import type { SeasonWindow } from '$lib/api/workspace-settings';
	import {
		canUpdateForecast,
		forecastFact,
		seasonFact,
		setupChips,
		skillFact
	} from '$lib/workspace/summary';
	import AreaControl from './AreaControl.svelte';
	import SeasonControl from './SeasonControl.svelte';

	const DEFAULT_SEASON_NOTE = 'Platform default dates; no season has been set for this workspace';

	interface Props {
		summary: WorkspaceSummary;
		/** Whether the assistant can be asked to act, e.g. to update the forecast. */
		canAct?: boolean;
		onUpdateForecast?: () => void;
		/** Opens the run a fact comes from. */
		onOpenJob?: (jobId: string) => void;
		/** Makes the season editable: reads the season in force, and saves a change. */
		seasonEditor?: {
			load: () => Promise<SeasonWindow>;
			save: (season: SeasonWindow | null) => Promise<void>;
		};
		/** The workspace's area and how to change it; without it the area is not shown. */
		areaEditor?: {
			area: FocusAreaValue | null;
			save: (area: FocusAreaValue | null) => Promise<void>;
		};
	}

	let {
		summary,
		canAct = false,
		onUpdateForecast,
		onOpenJob,
		seasonEditor,
		areaEditor
	}: Props = $props();

	const season = $derived(seasonFact(summary.season));
	const forecast = $derived(forecastFact(summary.latest_forecast));
	const skill = $derived(skillFact(summary.skill));
	const setup = $derived(setupChips(summary.setup).join(' · '));
	const forecastJob = $derived(summary.latest_forecast?.job_id ?? null);
	const skillJob = $derived(summary.skill?.job_id ?? null);
	const updatable = $derived(canAct && canUpdateForecast(summary.latest_forecast));
</script>

<!-- One line of facts; what each one rests on is a hover away rather than on screen. -->
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
	<span class="item" title={forecast.detail ?? undefined}>
		{#if forecastJob && onOpenJob}
			<button type="button" class="open" onclick={() => onOpenJob(forecastJob)}>
				{forecast.headline}
			</button>
		{:else}
			{forecast.headline}
		{/if}
		{#if updatable && onUpdateForecast}
			<button type="button" class="action" onclick={onUpdateForecast}>Update forecast</button>
		{/if}
	</span>
	<span class="item" title={skill.detail ?? undefined}>
		{#if skillJob && onOpenJob}
			<button type="button" class="open" onclick={() => onOpenJob(skillJob)}>
				{skill.headline}
			</button>
		{:else}
			{skill.headline}
		{/if}
	</span>
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
	.open,
	.action {
		padding: 0;
		border: 0;
		background: none;
		font: inherit;
		cursor: pointer;
	}
	.open {
		color: inherit;
		font-weight: 600;
	}
	.open:hover,
	.action:hover {
		text-decoration: underline;
	}
	.action {
		color: var(--color-accent);
		font-weight: 600;
	}
</style>
