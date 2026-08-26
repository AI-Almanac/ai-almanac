<script lang="ts">
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import Nav from '$lib/Nav.svelte';
	import Footer from '$lib/Footer.svelte';
	import ViewAsUserBanner from '$lib/components/ViewAsUserBanner.svelte';
	import { browser } from '$app/environment';
	import { afterNavigate, goto } from '$app/navigation';
	import { account } from '$lib/account.svelte';
	import { addBreadcrumb } from '$lib/breadcrumbs';

	let { children } = $props();

	afterNavigate((nav) => {
		addBreadcrumb('navigation', `→ ${nav.to?.url.pathname ?? '?'}`, {
			from: nav.from?.url.pathname,
			to: nav.to?.url.pathname,
			type: nav.type
		});
	});

	if (browser) {
		const path = window.location.pathname;
		const setupRequired = window.__ALMANAC_CONFIG__?.setupRequired;
		if (setupRequired && !path.startsWith('/setup')) {
			// Setup is required — redirect to wizard; skip nav/footer/account load
			goto('/setup');
		} else if (path !== '/callback' && !path.startsWith('/setup')) {
			// /callback runs the Globus token exchange itself. Bootstrapping auth here
			// would force a login redirect before a token exists, aborting the in-flight
			// exchange (NS_BINDING_ABORTED → "NetworkError") and looping the sign-in.
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
{@render children()}
{#if !isSetupPage}
	<Footer />
{/if}
