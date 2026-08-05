<script lang="ts">
	import { getContext } from 'svelte';
	import { page } from '$app/stores';
	import AdminGuard from '$lib/components/AdminGuard.svelte';
	import SettingsFields from '$lib/components/SettingsFields.svelte';
	import type { ConfigSettingsState } from '$lib/settings/config.svelte';
	import { sectionSlug } from '$lib/settings/sections';

	const settings = getContext<ConfigSettingsState>('settings');
	const group = $derived(
		settings.groups.find((g) => sectionSlug(g.name) === $page.params.section) ?? null
	);
</script>

<AdminGuard>
	<section class="card">
		{#if settings.loading}
			<p class="muted">Loading…</p>
		{:else if group}
			<SettingsFields {settings} {group} />
		{:else}
			<p class="muted">
				No settings section named <code>{$page.params.section}</code>. Pick one from the list on the
				left.
			</p>
		{/if}
	</section>
</AdminGuard>

<style>
	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
		padding: 1.1rem 1.25rem;
	}
	.muted {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.86rem;
	}
	code {
		font-size: 0.85em;
		padding: 0.1rem 0.35rem;
		border-radius: 0.25rem;
		background: var(--color-surface);
	}
</style>
