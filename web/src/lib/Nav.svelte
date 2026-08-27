<script lang="ts">
	import { page } from '$app/stores';
	import { account } from '$lib/account.svelte';
	import FeedbackWidget from '$lib/feedback/FeedbackWidget.svelte';

	let logoFailed = $state(false);
</script>

<nav class="site-nav">
	<div class="nav-inner">
		<div class="logo-row">
			<a href="/" class="brand">
				{#if logoFailed}
					<span>Laude</span>
					<span class="brand-mark">AI</span>
					<span>Almanac</span>
				{:else}
					<img
						class="brand-logo"
						src="/laude-ai-almanac-logo.png"
						alt="Laude AI Almanac"
						onerror={() => (logoFailed = true)}
					/>
				{/if}
			</a>
			<a href="https://uchicago.edu" target="_blank" rel="noopener noreferrer">
				<img
					class="uchicago-logo"
					src="/partners/uchicago-wordmark.svg"
					alt="University of Chicago"
				/>
			</a>
		</div>
		<div class="nav-row">
			<div class="links" aria-label="Primary navigation">
				<a href="/" class:active={$page.url.pathname === '/'}>Home</a>
				<a href="/almanac" class:active={$page.url.pathname.startsWith('/almanac')}>Almanac</a>
				<a href="/benchmarks" class:active={$page.url.pathname === '/benchmarks'}>Benchmarks</a>
				<a href="/blends" class:active={$page.url.pathname === '/blends'}>Blends</a>
				{#if account.canUseForecasting}
					<a href="/forecasts" class:active={$page.url.pathname === '/forecasts'}>Forecasts</a>
				{/if}
				{#if account.canUseForecasting && account.isAdmin}
					<a href="/forecast-data" class:active={$page.url.pathname.startsWith('/forecast-data')}>
						Forecast data
					</a>
				{/if}
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
					<a href="/settings" class:active={$page.url.pathname.startsWith('/settings')}>
						Settings
					</a>
				{/if}
				<a href="/user" class:active={$page.url.pathname.startsWith('/user')}>Account</a>
			</div>
			<div class="nav-right">
				<FeedbackWidget />
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
		</div>
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
		margin: 0 auto;
		padding: 0.75rem 0 0.5rem;
		display: flex;
		flex-direction: column;
		align-items: stretch;
		gap: 0.5rem;
	}

	.logo-row {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 1.5rem;
	}

	.nav-row {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.nav-right {
		position: absolute;
		right: 0;
		top: 50%;
		transform: translateY(-50%);
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

	.brand-logo {
		display: block;
		width: auto;
		height: 2rem;
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

	.nav-right {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.uchicago-logo {
		display: block;
		width: auto;
		height: 2.5rem;
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

	@media (max-width: 680px) {
		.nav-row {
			flex-wrap: wrap;
			justify-content: flex-start;
			gap: 0.5rem;
		}

		.nav-right {
			position: static;
			transform: none;
		}

		.links {
			width: 100%;
			overflow-x: auto;
		}
	}
</style>
