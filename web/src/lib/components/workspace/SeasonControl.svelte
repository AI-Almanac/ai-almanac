<script lang="ts">
	import type { SeasonWindow } from '$lib/api/workspace-settings';
	import type { SeasonInForce } from '$lib/workspace/season-form';
	import SeasonForm from './SeasonForm.svelte';
	import SettingPopover from './SettingPopover.svelte';

	interface Props {
		/** The season as the header states it. */
		headline: string;
		detail?: string | null;
		/** No season has been set here, so the platform's dates apply. */
		isDefault: boolean;
		/** The season in force and its year, to start the form from. */
		load: () => Promise<SeasonInForce>;
		save: (season: SeasonWindow | null, year: number) => Promise<void>;
	}

	let { headline, detail = null, isDefault, load, save }: Props = $props();

	let season = $state<SeasonInForce | null>(null);
	let loadError = $state(false);

	// Reopening the popover starts a new load; only the newest one may fill the form.
	let latestLoad = 0;

	async function loadSeason() {
		const thisLoad = ++latestLoad;
		season = null;
		loadError = false;
		try {
			const loaded = await load();
			if (thisLoad === latestLoad) season = loaded;
		} catch {
			if (thisLoad === latestLoad) loadError = true;
		}
	}
</script>

<SettingPopover
	label="Season"
	title={detail ? `${detail} · Change the season` : 'Change the season'}
	onOpen={loadSeason}
>
	{#snippet value()}
		{headline}
		{#if isDefault}<span class="dim">(default dates)</span>{/if}
	{/snippet}
	{#snippet children(close)}
		{#if season}
			<SeasonForm
				season={season.season}
				year={season.year}
				canReset={!isDefault}
				onSave={save}
				onClose={close}
			/>
		{:else if loadError}
			<p class="note">The season could not be loaded. Close this and try again.</p>
		{:else}
			<p class="note">Loading…</p>
		{/if}
	{/snippet}
</SettingPopover>

<style>
	.dim {
		color: var(--color-text-muted);
	}
	.note {
		margin: 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
</style>
