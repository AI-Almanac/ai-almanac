<script lang="ts">
	import { glossaryTerms } from '$lib/almanac/glossary';

	const termsByCategory = Object.groupBy(glossaryTerms, (term) => term.category);
</script>

<svelte:head>
	<title>Glossary | Almanac of Weather Models</title>
	<meta name="description" content="Climate and AI weather prediction glossary." />
</svelte:head>

<main class="glossary-page">
	<a class="back-link" href="/almanac">Back to almanac</a>
	<header>
		<p class="eyebrow">Reference terms</p>
		<h1>Glossary</h1>
		<p>Short definitions for climate, forecasting, data, evaluation, and AIWP terminology.</p>
	</header>

	<div class="term-groups">
		{#each Object.entries(termsByCategory) as [category, terms]}
			{#if terms}
				<section aria-labelledby={`${category}-title`}>
					<h2 id={`${category}-title`}>{category}</h2>
					<div class="term-list">
						{#each terms as term}
							<article id={term.slug}>
								<div class="term-heading">
									<h3>{term.term}</h3>
									<p>{term.shortDefinition}</p>
								</div>
								<p class="definition">{term.definition}</p>
								{#if term.relatedTerms.length}
									<p class="related">
										<span>Related</span>
										{term.relatedTerms.join(', ')}
									</p>
								{/if}
							</article>
						{/each}
					</div>
				</section>
			{/if}
		{/each}
	</div>
</main>

<style>
	.glossary-page {
		--almanac-rule: #bbb4a8;
		--almanac-ink: #2c2924;
		--almanac-red: #8b3f3d;
		width: min(100% - 3rem, 72rem);
		margin: 0 auto;
		padding: clamp(1rem, 3vw, 2rem) 0;
		color: var(--almanac-ink);
	}

	.back-link {
		display: inline-flex;
		margin-bottom: 0.65rem;
		color: #736c60;
		font-size: 0.74rem;
		font-weight: 800;
		letter-spacing: 0.12em;
		text-decoration: none;
		text-transform: uppercase;
	}

	header {
		display: grid;
		grid-template-columns: minmax(10rem, 13rem) minmax(0, 1fr);
		gap: 1rem;
		align-items: end;
		border-bottom: 0.0625rem solid var(--almanac-rule);
		padding-bottom: 0.6rem;
	}

	.eyebrow {
		margin: 0;
		color: var(--almanac-red);
		font-size: 0.68rem;
		font-weight: 800;
		letter-spacing: 0.14em;
		text-transform: uppercase;
	}

	header h1,
	header p {
		grid-column: 2;
	}

	h1,
	h2,
	h3,
	p {
		margin-top: 0;
	}

	h1 {
		margin-bottom: 0.2rem;
		font-size: clamp(1.45rem, 3vw, 2.35rem);
		line-height: 1;
		letter-spacing: 0;
	}

	header p {
		max-width: 36rem;
		margin-bottom: 0;
		color: #514c43;
		font-size: 0.9rem;
		font-weight: 600;
	}

	.term-groups {
		border-top: 0.0625rem solid var(--almanac-rule);
		margin-top: 0.55rem;
	}

	section {
		display: grid;
		grid-template-columns: minmax(10rem, 13rem) minmax(0, 1fr);
		gap: 1rem;
		border-bottom: 0.0625rem solid var(--almanac-rule);
		padding: 0.7rem 0;
	}

	h2 {
		color: var(--almanac-red);
		font-size: 0.68rem;
		font-weight: 800;
		letter-spacing: 0.14em;
		text-transform: uppercase;
	}

	.term-list {
		display: grid;
		gap: 0;
		border-top: 0.0625rem solid var(--almanac-rule);
	}

	article {
		display: grid;
		grid-template-columns: minmax(10rem, 14rem) minmax(0, 1fr) minmax(9rem, 12rem);
		gap: 0.8rem;
		border-bottom: 0.0625rem solid var(--almanac-rule);
		padding: 0.55rem 0;
	}

	article:last-child {
		border-bottom: 0;
	}

	h3 {
		margin-bottom: 0.18rem;
		font-size: 1rem;
		line-height: 1.1;
	}

	.term-heading p,
	.definition,
	.related {
		margin-bottom: 0;
		color: #514c43;
		font-size: 0.84rem;
		font-weight: 600;
		line-height: 1.35;
	}

	.related span {
		display: block;
		color: #736c60;
		font-size: 0.62rem;
		font-weight: 800;
		letter-spacing: 0.12em;
		text-transform: uppercase;
	}

	@media (max-width: 860px) {
		.glossary-page {
			width: min(100% - 2rem, 72rem);
		}

		header,
		section,
		article {
			grid-template-columns: 1fr;
		}

		header h1,
		header p {
			grid-column: auto;
		}
	}
</style>
