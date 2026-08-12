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
		width: min(100% - 3rem, 72rem);
		margin: 0 auto;
		padding: clamp(1rem, 3vw, 2rem) 0;
		color: var(--color-text);
	}

	.back-link {
		display: inline-flex;
		margin-bottom: 0.65rem;
		color: var(--color-text-muted);
		font-size: 0.8rem;
		font-weight: 650;
		text-decoration: none;
	}

	.back-link:hover {
		color: var(--color-text);
		text-decoration: underline;
	}

	header {
		display: grid;
		grid-template-columns: minmax(10rem, 13rem) minmax(0, 1fr);
		gap: 1rem;
		align-items: end;
		border-bottom: 0.0625rem solid var(--color-border);
		padding-bottom: 0.6rem;
	}

	.eyebrow {
		margin: 0;
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 750;
		letter-spacing: 0.1em;
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
		font-family: var(--font-display);
		font-size: clamp(1.5rem, 3vw, 1.85rem);
		font-weight: 550;
		line-height: 1.2;
		letter-spacing: -0.025em;
	}

	header p {
		max-width: 36rem;
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.92rem;
		line-height: 1.5;
	}

	.term-groups {
		border-top: 0.0625rem solid var(--color-border);
		margin-top: 0.55rem;
	}

	section {
		display: grid;
		grid-template-columns: minmax(10rem, 13rem) minmax(0, 1fr);
		gap: 1rem;
		border-bottom: 0.0625rem solid var(--color-border);
		padding: 0.7rem 0;
	}

	h2 {
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}

	.term-list {
		display: grid;
		gap: 0;
		border-top: 0.0625rem solid var(--color-border-subtle);
	}

	article {
		display: grid;
		grid-template-columns: minmax(10rem, 14rem) minmax(0, 1fr) minmax(9rem, 12rem);
		gap: 0.8rem;
		border-bottom: 0.0625rem solid var(--color-border-subtle);
		padding: 0.55rem 0;
	}

	article:last-child {
		border-bottom: 0;
	}

	h3 {
		margin-bottom: 0.18rem;
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 650;
		line-height: 1.3;
		letter-spacing: -0.015em;
	}

	.term-heading p,
	.definition,
	.related {
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.88rem;
		line-height: 1.45;
	}

	.related span {
		display: block;
		color: var(--color-text-dim);
		font-size: 0.68rem;
		font-weight: 750;
		letter-spacing: 0.1em;
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
