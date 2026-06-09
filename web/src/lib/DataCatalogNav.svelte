<script lang="ts">
	import { page } from '$app/stores';

	const items = [
		{
			href: '/regions',
			index: '01',
			label: 'Regions',
			description: 'Define reusable geographic coverage and benchmark bounds.'
		},
		{
			href: '/data-sources',
			index: '02',
			label: 'Data sources',
			description: 'Connect observation datasets and model forecast directories.'
		}
	];

	function isActive(href: string): boolean {
		return $page.url.pathname.startsWith(href);
	}
</script>

<section class="catalog-header" aria-labelledby="data-catalog-title">
	<div class="catalog-intro">
		<div>
			<p class="eyebrow">Workspace</p>
			<h1 id="data-catalog-title">Data catalog</h1>
		</div>
		<p>
			Manage the two building blocks used by benchmarks: where an analysis takes place and which
			files provide observations and forecasts.
		</p>
	</div>

	<nav class="catalog-nav" aria-label="Data catalog">
		{#each items as item}
			<a
				href={item.href}
				class:active={isActive(item.href)}
				aria-current={isActive(item.href) ? 'page' : undefined}
			>
				<span class="index">{item.index}</span>
				<span class="copy">
					<strong>{item.label}</strong>
					<small>{item.description}</small>
				</span>
				<span class="arrow" aria-hidden="true">→</span>
			</a>
		{/each}
	</nav>
</section>

<style>
	.catalog-header {
		display: flex;
		flex-direction: column;
		gap: 1.35rem;
	}
	.catalog-intro {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 2rem;
	}
	.catalog-intro h1,
	.catalog-intro p {
		margin: 0;
	}
	.catalog-intro h1 {
		font-family: var(--font-display);
		font-size: clamp(2rem, 5vw, 3.25rem);
		font-weight: 500;
		letter-spacing: -0.045em;
		line-height: 1;
	}
	.catalog-intro > p {
		max-width: 38rem;
		color: var(--color-text-muted);
	}
	.eyebrow {
		margin-bottom: 0.35rem !important;
		color: var(--color-accent);
		font-size: 0.7rem;
		font-weight: 750;
		letter-spacing: 0.11em;
		text-transform: uppercase;
	}
	.catalog-nav {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.7rem;
		padding: 0.35rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.85rem;
		background: color-mix(in oklab, var(--color-surface-muted) 48%, transparent);
	}
	.catalog-nav a {
		display: flex;
		align-items: center;
		gap: 0.9rem;
		min-width: 0;
		padding: 0.9rem 1rem;
		border: 0.0625rem solid transparent;
		border-radius: 0.6rem;
		color: var(--color-text-muted);
		text-decoration: none;
		transition:
			border-color 120ms ease,
			background-color 120ms ease,
			color 120ms ease,
			transform 120ms ease;
	}
	.catalog-nav a:hover {
		color: var(--color-text);
		transform: translateY(-0.0625rem);
	}
	.catalog-nav a.active {
		border-color: var(--color-accent-border);
		background: var(--color-surface-raised);
		box-shadow: 0 0.35rem 1.25rem rgba(36, 33, 29, 0.06);
		color: var(--color-text);
	}
	.index {
		color: var(--color-accent);
		font-family: var(--font-mono);
		font-size: 0.7rem;
		font-weight: 600;
	}
	.copy {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-width: 0;
	}
	.copy strong {
		font-size: 0.98rem;
	}
	.copy small {
		color: var(--color-text-muted);
		font-size: 0.76rem;
		line-height: 1.35;
	}
	.arrow {
		color: var(--color-text-dim);
		font-size: 1.1rem;
		transition: transform 120ms ease;
	}
	.catalog-nav a:hover .arrow,
	.catalog-nav a.active .arrow {
		color: var(--color-accent);
		transform: translateX(0.12rem);
	}
	@media (max-width: 700px) {
		.catalog-intro {
			align-items: flex-start;
			flex-direction: column;
			gap: 0.75rem;
		}
		.catalog-nav {
			grid-template-columns: 1fr;
		}
	}
</style>
