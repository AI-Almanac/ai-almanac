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
		--almanac-rule: #bbb4a8;
		--almanac-ink: #2c2924;
		--almanac-red: #8b3f3d;
		width: min(100% - 3rem, 58rem);
		margin: 0 auto;
		padding: clamp(1.5rem, 4vw, 2.75rem) 0;
		color: var(--almanac-ink);
	}

	.hero {
		border-bottom: 0.0625rem solid var(--almanac-rule);
		display: grid;
		grid-template-columns: minmax(15rem, 21rem) minmax(17rem, 1fr);
		column-gap: clamp(1rem, 4vw, 2.4rem);
		row-gap: 0.45rem;
		align-items: center;
		padding: 0 0 0.9rem;
	}

	.eyebrow {
		grid-column: 1 / -1;
		margin: 0;
		color: var(--almanac-red);
		font-size: 0.68rem;
		font-weight: 800;
		letter-spacing: 0.14em;
		text-transform: uppercase;
	}

	h1,
	p {
		margin-top: 0;
	}

	h1 {
		max-width: 21rem;
		margin-bottom: 0;
		font-size: clamp(1.65rem, 3.5vw, 2.45rem);
		font-weight: 800;
		line-height: 1;
		letter-spacing: 0;
	}

	.lede {
		max-width: 29rem;
		margin-bottom: 0;
		color: #514c43;
		font-size: 0.88rem;
		font-weight: 600;
		line-height: 1.45;
	}

	.directory {
		display: grid;
		margin-top: 0.75rem;
		border-top: 0.0625rem solid var(--almanac-rule);
	}

	.directory a {
		display: grid;
		grid-template-columns: minmax(9rem, 11.5rem) minmax(0, 1fr) 5rem;
		gap: 0.75rem;
		align-items: baseline;
		border-bottom: 0.0625rem solid var(--almanac-rule);
		color: var(--almanac-ink);
		padding: 0.52rem 0;
		text-decoration: none;
	}

	.directory a:hover .section-name {
		color: var(--almanac-red);
	}

	.section-name {
		font-size: 0.98rem;
		font-weight: 800;
	}

	.section-description {
		color: #514c43;
		font-size: 0.84rem;
		font-weight: 600;
		line-height: 1.35;
	}

	.section-count {
		color: #736c60;
		font-family: var(--font-mono);
		font-size: 0.72rem;
		font-weight: 700;
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
