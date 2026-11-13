<script lang="ts">
	import type { SeasonWindow } from '$lib/api/workspace-settings';
	import SeasonForm from './SeasonForm.svelte';
	import SettingPopover from './SettingPopover.svelte';

	interface Props {
		/** The season as the header states it. */
		headline: string;
		detail?: string | null;
		/** No season has been set here, so the platform's dates apply. */
		isDefault: boolean;
		/** The season in force, to start the form from. */
		load: () => Promise<SeasonWindow>;
		save: (season: SeasonWindow | null) => Promise<void>;
	}

	let { headline, detail = null, isDefault, load, save }: Props = $props();

	let season = $state<SeasonWindow | null>(null);
	let loadError = $state(false);

	async function loadSeason() {
		season = null;
		loadError = false;
		try {
			season = await load();
		} catch {
			loadError = true;
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
			<SeasonForm {season} canReset={!isDefault} onSave={save} onClose={close} />
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
