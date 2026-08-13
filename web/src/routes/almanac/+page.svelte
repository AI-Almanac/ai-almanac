<script lang="ts">
	import { almanacSections } from '$lib/almanac/catalog';
</script>

<svelte:head>
	<title>Almanac of Weather Models | AI Almanac</title>
	<meta
		name="description"
		content="Static reference pages for weather model families, datasets, and architectures."
	/>
</svelte:head>

<main class="almanac">
	<section class="hero">
		<p class="eyebrow">Reference catalog</p>
		<h1>Almanac of Weather Models</h1>
		<p class="lede">
			A compact guide to model families, benchmark datasets, and architecture patterns used in AI
			weather forecasting.
		</p>
	</section>

	<nav class="directory" aria-label="Almanac sections">
		{#each almanacSections as section}
			<a href={section.href}>
				<span class="section-name">{section.title}</span>
				<span class="section-description">{section.description}</span>
				<span class="section-count">{section.count} entries</span>
			</a>
		{/each}
	</nav>
</main>

<style>
	.almanac {
		width: min(100% - 3rem, 58rem);
		margin: 0 auto;
		padding: clamp(1.5rem, 4vw, 2.75rem) 0;
		color: var(--color-text);
	}

	.hero {
		display: grid;
		grid-template-columns: minmax(15rem, 21rem) minmax(17rem, 1fr);
		column-gap: clamp(1rem, 4vw, 2.4rem);
		row-gap: 0.45rem;
		align-items: center;
		padding: 0 0 1.4rem;
	}

	.eyebrow {
		grid-column: 1 / -1;
		margin: 0;
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}

	h1,
	p {
		margin-top: 0;
	}

	h1 {
		max-width: 21rem;
		margin-bottom: 0;
		font-family: var(--font-display);
		font-size: clamp(1.5rem, 3.5vw, 1.85rem);
		font-weight: 550;
		line-height: 1.2;
		letter-spacing: -0.025em;
	}

	.lede {
		max-width: 29rem;
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.92rem;
		line-height: 1.5;
	}

	.directory {
		display: grid;
		border-top: 0.0625rem solid var(--color-border);
	}

	.directory a {
		display: grid;
		grid-template-columns: minmax(9rem, 11.5rem) minmax(0, 1fr) 5rem;
		gap: 0.75rem;
		align-items: baseline;
		border-bottom: 0.0625rem solid var(--color-border);
		color: var(--color-text);
		padding: 0.52rem 0;
		text-decoration: none;
	}

	.directory a:hover .section-name {
		color: var(--color-accent);
	}

	.section-name {
		font-size: 0.98rem;
		font-weight: 650;
	}

	.section-description {
		color: var(--color-text-muted);
		font-size: 0.88rem;
		line-height: 1.4;
	}

	.section-count {
		color: var(--color-text-dim);
		font-family: var(--font-mono);
		font-size: 0.72rem;
		text-align: right;
		white-space: nowrap;
	}

	@media (max-width: 720px) {
		.almanac {
			width: min(100% - 2rem, 58rem);
		}

		.hero {
			grid-template-columns: 1fr;
			gap: 0.45rem;
		}

		.directory a {
			grid-template-columns: 1fr auto;
		}

		.section-description {
			grid-column: 1 / -1;
		}
	}
</style>
