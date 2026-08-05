<script lang="ts">
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import Nav from '$lib/Nav.svelte';
	import Footer from '$lib/Footer.svelte';
	import ViewAsUserBanner from '$lib/components/ViewAsUserBanner.svelte';
	import { browser } from '$app/environment';
	import { afterNavigate } from '$app/navigation';
	import { account } from '$lib/account.svelte';
	import { addBreadcrumb } from '$lib/breadcrumbs';
	import FeedbackWidget from '$lib/feedback/FeedbackWidget.svelte';

	let { children } = $props();

	afterNavigate((nav) => {
		addBreadcrumb('navigation', `→ ${nav.to?.url.pathname ?? '?'}`, {
			from: nav.from?.url.pathname,
			to: nav.to?.url.pathname,
			type: nav.type
		});
	});

	// /callback runs the Globus token exchange itself. Bootstrapping auth here
	// would force a login redirect before a token exists, aborting the in-flight
	// exchange (NS_BINDING_ABORTED → "NetworkError") and looping the sign-in.
	if (browser && window.location.pathname !== '/callback') {
		account.load();
	}
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>
<ViewAsUserBanner />
<Nav />
{@render children()}
<FeedbackWidget />
<Footer />
