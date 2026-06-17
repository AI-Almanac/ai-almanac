<script lang="ts">
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import Nav from '$lib/Nav.svelte';
	import Footer from '$lib/Footer.svelte';
	import { browser } from '$app/environment';
	import { account } from '$lib/account.svelte';

	let { children } = $props();

	// /callback runs the Globus token exchange itself. Bootstrapping auth here
	// would force a login redirect before a token exists, aborting the in-flight
	// exchange (NS_BINDING_ABORTED → "NetworkError") and looping the sign-in.
	if (browser && window.location.pathname !== '/callback') {
		account.load();
	}
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>
<Nav />
{@render children()}
<Footer />
