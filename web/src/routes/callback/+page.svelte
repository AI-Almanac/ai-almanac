<script lang="ts">
	import { onMount } from 'svelte';
	import { getManager } from '$lib/auth';
	import { account } from '$lib/account.svelte';

	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			await getManager()?.handleCodeRedirect();
			await account.reload();
			const returnTo = sessionStorage.getItem('auth_return_to') ?? '/';
			sessionStorage.removeItem('auth_return_to');
			window.location.replace(returnTo);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Authentication failed.';
		}
	});
</script>

{#if error}
	<section class="callback-state">
		<h1>Authentication Error</h1>
		<p>{error}</p>
		<a href="/">Return home</a>
	</section>
{:else}
	<section class="callback-state">
		<p>Completing sign in...</p>
	</section>
{/if}

<style>
	.callback-state {
		min-height: calc(100vh - 9rem);
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.75rem;
		padding: 2rem;
		color: var(--color-text-muted);
	}

	h1 {
		margin: 0;
		color: var(--color-text);
		font-size: 1.25rem;
	}

	p {
		margin: 0;
	}

	a {
		color: var(--color-accent);
		font-weight: 700;
	}
</style>
