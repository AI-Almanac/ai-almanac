<script lang="ts">
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import Nav from '$lib/Nav.svelte';
	import Footer from '$lib/Footer.svelte';
	import ViewAsUserBanner from '$lib/components/ViewAsUserBanner.svelte';
	import LoginPrompt from '$lib/LoginPrompt.svelte';
	import { browser } from '$app/environment';
	import { afterNavigate, goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { isAnonRoute } from '$lib/anon-routes';
	import { account } from '$lib/account.svelte';
	import { addBreadcrumb } from '$lib/breadcrumbs';
	import { isAuthenticated } from '$lib/auth-store';
	import { USE_BEARER_AUTH } from '$lib/api/core';

	let { children } = $props();

	// /callback must always render: it runs the Globus token exchange, so no
	// token exists yet while it is on screen. It always arrives and leaves via
	// full page loads, so a load-time check is enough.
	const isCallback = browser && window.location.pathname === '/callback';

	afterNavigate((nav) => {
		addBreadcrumb('navigation', `→ ${nav.to?.url?.pathname ?? '?'}`, {
			from: nav.from?.url?.pathname,
			to: nav.to?.url?.pathname,
			type: nav.type
		});
	});

	if (browser) {
		const path = window.location.pathname;
		const setupRequired = window.__ALMANAC_CONFIG__?.setupRequired;
		if (setupRequired && !path.startsWith('/setup')) {
			// Setup is required — redirect to wizard; skip nav/footer/account load
			goto('/setup');
		} else if (!isCallback && !path.startsWith('/setup')) {
			account.load();
		}
	}

	const isSetupPage = $derived(browser ? window.location.pathname.startsWith('/setup') : false);
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>
{#if !isSetupPage}
	<ViewAsUserBanner />
	<Nav />
{/if}
{#if browser && USE_BEARER_AUTH && !isCallback && !isSetupPage && !$isAuthenticated && !isAnonRoute($page.url.pathname)}
	<LoginPrompt />
{:else}
	{@render children()}
{/if}
{#if !isSetupPage}
	<Footer />
{/if}
