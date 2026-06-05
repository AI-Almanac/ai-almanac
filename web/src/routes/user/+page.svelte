<script lang="ts">
	import { getManager } from '$lib/auth';
	import { isAuthenticated } from '$lib/auth-store';
	import LoginPrompt from '$lib/LoginPrompt.svelte';

	function logout() {
		getManager()?.revoke();
	}

	let user = $derived(getManager()?.user ?? null);
</script>

{#if $isAuthenticated}
	<div class="flex min-h-[calc(100vh-3.5rem)] justify-center px-6 pt-12">
		<div
			class="flex h-fit w-full max-w-[400px] flex-col gap-1.5 rounded-[0.6rem] border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] p-8"
		>
			<p class="eyebrow">Account</p>
			<h1 class="name">{user?.name ?? 'User'}</h1>
			{#if user?.email}
				<p class="m-0 text-[0.875rem] text-[var(--color-text-muted)]">{user.email}</p>
			{/if}
			<hr class="my-3 border-t border-none border-[var(--color-border-subtle)]" />
			<button class="btn-danger" onclick={logout}>Sign out</button>
		</div>
	</div>
{:else}
	<LoginPrompt message="Sign in to view your account." />
{/if}

<style>
	.eyebrow {
		font-size: 0.65rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--color-accent);
		margin: 0;
		opacity: 0.8;
	}

	.name {
		font-family: var(--font-display);
		font-size: 1.75rem;
		font-weight: 400;
		font-style: italic;
		letter-spacing: -0.01em;
		color: var(--color-text);
		margin: 0;
	}

	.btn-danger {
		align-self: flex-start;
		padding: 0.5rem 1.1rem;
		background: transparent;
		color: var(--color-danger);
		border: 1px solid var(--color-danger-border);
		border-radius: 0.4rem;
		font-family: var(--font-body);
		font-size: 0.875rem;
		font-weight: 500;
		cursor: pointer;
		transition:
			background-color 0.12s,
			border-color 0.12s;
	}

	.btn-danger:hover {
		background-color: var(--color-danger-bg);
		border-color: var(--color-danger);
	}
</style>
