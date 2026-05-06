<script lang="ts">
	import { page } from '$app/stores';
</script>

<nav class="site-nav">
	<div class="nav-inner">
		<a href="/" class="brand">
			<span class="brand-mark">AI</span>
			<span>Almanac</span>
		</a>
		<div class="links" aria-label="Primary navigation">
			<a href="/" class:active={$page.url.pathname === '/'}>Home</a>
			<a href="/benchmarks" class:active={$page.url.pathname === '/benchmarks'}>Runs</a>
			<a href="/user" class:active={$page.url.pathname === '/user'}>Account</a>
		</div>
	</div>
</nav>

<style>
	.site-nav {
		position: sticky;
		top: 0;
		z-index: 50;
		border-bottom: 1px solid var(--color-border);
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
		.nav-inner {
			align-items: flex-start;
			flex-direction: column;
			padding: 0.8rem 0;
		}

		.links {
			width: 100%;
			overflow-x: auto;
		}
	}
</style>
