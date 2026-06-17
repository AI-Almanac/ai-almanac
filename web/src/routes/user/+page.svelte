<script lang="ts">
	import { getManager } from '$lib/auth';
	import { isAuthenticated } from '$lib/auth-store';
	import { account } from '$lib/account.svelte';
	import LoginPrompt from '$lib/LoginPrompt.svelte';

	async function logout() {
		await getManager()?.revoke();
		account.clear();
	}

	// Load the account once per authenticated session. Without the `attempted`
	// guard a failing /auth/me leaves account null, and because the effect reads
	// account/loading it would re-fire and retry forever (CPU spin + request storm).
	let attempted = $state(false);
	$effect(() => {
		if (!$isAuthenticated) {
			attempted = false;
			return;
		}
		if (!attempted && !account.account && !account.loading) {
			attempted = true;
			void account.reload();
		}
	});
</script>

{#if $isAuthenticated}
	<section class="account-page">
		<div class="account-panel">
			<p class="eyebrow">Account</p>
			<h1>{account.label || 'User'}</h1>
			{#if account.account?.email}
				<p class="email">{account.account.email}</p>
			{/if}
			{#if account.isAdmin}
				<span class="role">Admin</span>
			{/if}
			<a class="settings-link" href="/settings/ai">AI assistant settings</a>
			<button type="button" onclick={logout}>Sign out</button>
		</div>
	</section>
{:else}
	<LoginPrompt message="Sign in to view your account." />
{/if}

<style>
	.account-page {
		min-height: calc(100vh - 9rem);
		display: flex;
		justify-content: center;
		padding: 3rem 1rem;
	}

	.account-panel {
		width: min(100%, 26rem);
		height: fit-content;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		padding: 2rem;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.eyebrow,
	.email {
		margin: 0;
		color: var(--color-text-muted);
	}

	.eyebrow {
		font-size: 0.72rem;
		font-weight: 800;
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}

	h1 {
		margin: 0;
		color: var(--color-text);
		font-size: clamp(1.35rem, 4vw, 1.9rem);
	}

	.role {
		width: fit-content;
		border-radius: 0.35rem;
		background: var(--color-accent);
		color: white;
		font-size: 0.75rem;
		font-weight: 800;
		padding: 0.18rem 0.5rem;
		text-transform: uppercase;
	}

	.settings-link {
		width: fit-content;
		color: var(--color-text);
		font-weight: 650;
		text-decoration: underline;
	}

	button {
		width: fit-content;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.45rem;
		background: transparent;
		color: var(--color-text);
		cursor: pointer;
		font-weight: 750;
		padding: 0.55rem 0.9rem;
	}

	button:hover {
		background: var(--color-surface-muted);
	}
</style>
