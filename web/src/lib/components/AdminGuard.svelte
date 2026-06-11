<script lang="ts">
	import type { Snippet } from 'svelte';
	import { account } from '$lib/account.svelte';

	interface Props {
		children: Snippet;
	}

	const { children }: Props = $props();
</script>

{#if !account.loaded}
	<!-- Wait for /auth/me before deciding what to render. -->
{:else if account.isAdmin}
	{@render children()}
{:else}
	<main class="forbidden">
		<h1>Administrators only</h1>
		<p>This page manages the deployment and is only available to administrators.</p>
		<a href="/">Back to home</a>
	</main>
{/if}

<style>
	.forbidden {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.75rem;
		min-height: 50vh;
		padding: clamp(1.5rem, 5vw, 4rem);
		text-align: center;
	}

	.forbidden h1 {
		margin: 0;
		font-family: var(--font-display);
		font-size: clamp(1.5rem, 3vw, 2rem);
		color: var(--color-text);
	}

	.forbidden p {
		margin: 0;
		max-width: 28rem;
		color: var(--color-text-muted);
	}

	.forbidden a {
		color: var(--color-accent);
		font-weight: 700;
	}
</style>
