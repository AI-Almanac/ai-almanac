<script lang="ts">
	import { setContext, type Snippet } from 'svelte';
	import { page } from '$app/stores';
	import { account } from '$lib/account.svelte';
	import { ConfigSettingsState } from '$lib/settings/config.svelte';
	import { settingsNav } from '$lib/settings/sections';

	const { children }: { children: Snippet } = $props();

	// One fetch for the whole settings area: the nav needs the group list and
	// each section page edits one of those groups.
	const settings = new ConfigSettingsState();
	setContext('settings', settings);

	// Only admins may read the platform config; a non-admin reaches this area for
	// their own API keys alone, so loading it would just 403 into a banner.
	let configRequested = false;
	$effect(() => {
		if (!account.loaded || configRequested || !account.isAdmin) return;
		configRequested = true;
		void settings.load();
	});

	// The flag lives in the settings the shell already loaded, so the nav needs no
	// extra request. Absent (not yet loaded) reads as on, matching the default.
	const nav = $derived(
		settingsNav(settings.groups, {
			comparisonsEnabled: settings.value('assistant_comparisons_audience') !== 'off'
		})
	);
	const currentPath = $derived($page.url.pathname);

	function isCurrent(href: string): boolean {
		return href === '/settings' ? currentPath === '/settings' : currentPath === href;
	}
</script>

<svelte:head><title>Settings · AI Almanac</title></svelte:head>

<div class="settings-shell">
	<aside class="sidebar">
		<h1>Settings</h1>
		{#each nav as group (group.heading)}
			{@const links = group.links.filter((link) => account.isAdmin || !link.adminOnly)}
			{#if links.length}
				<nav aria-label={group.heading}>
					<h2>{group.heading}</h2>
					<ul>
						{#each links as link (link.href)}
							<li>
								<a href={link.href} class:current={isCurrent(link.href)}>{link.label}</a>
							</li>
						{/each}
					</ul>
				</nav>
			{/if}
		{/each}

		<!-- Hidden while previewing, so the sidebar looks exactly like a user's.
		     The banner above the nav is the way back out. -->
		{#if account.isActuallyAdmin && !account.viewingAsUser}
			<div class="preview-toggle">
				<button onclick={() => account.setViewingAsUser(true)}>View as a regular user</button>
				<p>
					Hides admin-only navigation and controls so you can check what everyone else sees. Server
					permissions are unchanged, and reloading the page ends the preview.
				</p>
			</div>
		{/if}
	</aside>

	<main class="content">
		{#if settings.error}
			<p class="banner err">{settings.error}</p>
		{/if}
		{@render children()}
	</main>
</div>

<style>
	.settings-shell {
		display: flex;
		align-items: flex-start;
		gap: 2rem;
		width: min(100% - 2rem, 76rem);
		margin: 2rem auto 4rem;
	}

	.sidebar {
		position: sticky;
		top: 5rem;
		flex: 0 0 14rem;
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}
	.sidebar h1 {
		margin: 0;
		font-size: 1.3rem;
	}
	.sidebar h2 {
		margin: 0 0 0.4rem 0.75rem;
		font-size: 0.68rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--color-text-muted);
	}
	.sidebar ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.sidebar a {
		display: block;
		padding: 0.4rem 0.75rem;
		border-radius: 999px;
		color: var(--color-text);
		font-size: 0.86rem;
		text-decoration: none;
		transition:
			background-color 0.12s,
			color 0.12s;
	}
	.sidebar a:hover {
		background: var(--color-accent-glow);
	}
	.sidebar a.current {
		background: var(--color-accent-light);
		color: var(--color-accent);
		font-weight: 600;
	}

	.content {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}

	.preview-toggle {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding-top: 0.9rem;
		border-top: 1px solid var(--color-border-subtle);
	}
	.preview-toggle button {
		padding: 0.35rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 999px;
		background: transparent;
		color: var(--color-text);
		font: inherit;
		font-size: 0.8rem;
		font-weight: 600;
		cursor: pointer;
	}
	.preview-toggle button:hover {
		border-color: var(--color-accent);
		color: var(--color-accent);
		background: var(--color-accent-light);
	}
	.preview-toggle p {
		margin: 0;
		font-size: 0.7rem;
		line-height: 1.4;
		color: var(--color-text-muted);
	}

	.banner.err {
		margin: 0;
		padding: 0.75rem 1rem;
		border-radius: 0.5rem;
		color: var(--color-danger);
		background: var(--color-danger-bg);
		border: 1px solid var(--color-danger);
	}

	/* Shared chrome for every settings section, so the area reads as one
	   surface instead of each page inventing its own card. */
	.content :global(.card) {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
		padding: 1.1rem 1.25rem;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.content :global(.card > h2) {
		margin: 0;
		font-size: 1.05rem;
	}
	.content :global(.card > h3) {
		margin: 0.5rem 0 0;
		font-size: 0.88rem;
	}
	.content :global(.hint) {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.82rem;
		line-height: 1.5;
	}
	.content :global(.empty) {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.86rem;
	}
	.content :global(.tag) {
		font-size: 0.62rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		padding: 0.1rem 0.35rem;
		border-radius: 3px;
		border: 1px solid var(--color-border);
		color: var(--color-text-muted);
		white-space: nowrap;
	}
	.content :global(.tag.active) {
		color: var(--color-accent);
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}
	.content :global(.tag.exposed),
	.content :global(.tag.required) {
		color: var(--color-status-running);
		border-color: var(--color-status-running);
	}
	.content :global(.tag.preview) {
		color: var(--color-status-pending, var(--color-text-muted));
		border-color: currentcolor;
	}

	/* Narrow viewports: the nav becomes a scrollable strip above the content
	   rather than a column that squeezes it. */
	@media (max-width: 52rem) {
		.settings-shell {
			flex-direction: column;
			gap: 1.25rem;
		}
		.sidebar {
			position: static;
			flex: none;
			width: 100%;
		}
		.sidebar ul {
			flex-direction: row;
			flex-wrap: wrap;
		}
		.content {
			width: 100%;
		}
	}
</style>
