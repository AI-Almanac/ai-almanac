<script lang="ts">
	import { page } from '$app/stores';
	import { account } from '$lib/account.svelte';
</script>

<nav class="site-nav" class:almanac-nav={$page.url.pathname.startsWith('/almanac')}>
	<div class="nav-inner">
		<a href="/" class="brand">
			<span class="brand-mark">AI</span>
			<span>Almanac</span>
		</a>
		<div class="links" aria-label="Primary navigation">
			<a href="/" class:active={$page.url.pathname === '/'}>Home</a>
			<a href="/almanac" class:active={$page.url.pathname.startsWith('/almanac')}>Almanac</a>
			<a href="/benchmarks" class:active={$page.url.pathname === '/benchmarks'}>Benchmarks</a>
			<a href="/blends" class:active={$page.url.pathname === '/blends'}>Blends</a>
			<a href="/forecasts" class:active={$page.url.pathname === '/forecasts'}>Forecasts</a>
			{#if account.canManageData}
				<a
					href="/data-sources"
					class:active={$page.url.pathname.startsWith('/data-sources') ||
						$page.url.pathname.startsWith('/regions')}
				>
					Data
				</a>
			{/if}
			{#if account.isAdmin}
				<a href="/settings" class:active={$page.url.pathname.startsWith('/settings')}> Settings </a>
			{/if}
			<a href="/user" class:active={$page.url.pathname.startsWith('/user')}>Account</a>
		</div>
		{#if account.loaded && account.isShared}
			<a
				class="account"
				href="/user"
				title={account.account?.email ?? account.account?.subject ?? ''}
			>
				<span class="account-name">{account.label}</span>
				{#if account.isAdmin}<span class="role-badge">admin</span>{/if}
			</a>
		{/if}
	</div>
</nav>

<style>
	.site-nav {
		position: sticky;
		top: 0;
		z-index: 50;
		border-bottom: 0.0625rem solid var(--color-border);
		background: var(--color-bg-glass);
		backdrop-filter: blur(1rem);
		-webkit-backdrop-filter: blur(1rem);
	}

	.nav-inner {
		width: min(100% - 2rem, 76rem);
		min-height: 4rem;
		margin: 0 auto;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}

	.brand,
	.links {
		display: flex;
		align-items: center;
	}

	.brand {
		gap: 0.65rem;
		color: var(--color-text);
		font-weight: 800;
		text-decoration: none;
	}

	.brand-mark {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		aspect-ratio: 1;
		border-radius: 0.45rem;
		background: var(--color-accent);
		color: white;
		font-weight: 800;
	}

	.links {
		gap: 0.25rem;
	}

	.account {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--color-text-muted);
		font-size: 0.85rem;
		font-weight: 650;
		text-decoration: none;
		white-space: nowrap;
	}

	.account:hover {
		color: var(--color-text);
	}

	.account-name {
		max-width: 12rem;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.role-badge {
		padding: 0.1rem 0.4rem;
		border-radius: 0.3rem;
		background: var(--color-accent);
		color: white;
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.links a {
		color: var(--color-text-muted);
		text-decoration: none;
		font-size: 0.92rem;
		font-weight: 650;
		padding: 0.45rem 0.7rem;
		border-radius: 0.4rem;
	}

	.links a:hover,
	.links a.active {
		color: var(--color-text);
		background: var(--color-surface-muted);
	}

	.almanac-nav {
		position: static;
		border-bottom-color: #bbb4a8;
		background: #f7f4ef;
		backdrop-filter: none;
		-webkit-backdrop-filter: none;
	}

	.almanac-nav .nav-inner {
		width: min(100% - 4rem, 94rem);
		min-height: 4.5rem;
	}

	.almanac-nav .brand,
	.almanac-nav .links a {
		font-weight: 800;
		letter-spacing: 0;
		text-transform: none;
	}

	.almanac-nav .brand {
		font-size: 0.92rem;
	}

	.almanac-nav .brand-mark {
		width: 1.45rem;
		border: 0.0625rem solid #8b3f3d;
		border-radius: 0;
		background: transparent;
		color: #8b3f3d;
		font-family: var(--font-mono);
		font-size: 0.68rem;
	}

	.almanac-nav .links {
		gap: 1.1rem;
	}

	.almanac-nav .links a {
		border-bottom: 0.14rem solid transparent;
		border-radius: 0;
		color: #2c2924;
		font-size: 0.92rem;
		padding: 0.3rem 0 0.55rem;
	}

	.almanac-nav .links a:hover,
	.almanac-nav .links a.active {
		background: transparent;
		border-bottom-color: #8b3f3d;
		color: #8b3f3d;
	}

	@media (max-width: 680px) {
		.nav-inner {
			align-items: flex-start;
			flex-direction: column;
			padding: 0.8rem 0;
		}

		.links {
			width: 100%;
			overflow-x: auto;
		}

		.almanac-nav .nav-inner {
			width: min(100% - 2rem, 94rem);
		}
	}
</style>
