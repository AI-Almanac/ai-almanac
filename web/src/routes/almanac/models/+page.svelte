<script lang="ts">
	import { modelFamilies } from '$lib/almanac/content';
</script>

<svelte:head>
	<title>Model Families | Almanac of Weather Models</title>
	<meta name="description" content="Condensed index of weather model families." />
</svelte:head>

<main class="index-page">
	<a class="back-link" href="/almanac">Back to almanac</a>
	<header>
		<p class="eyebrow">Forecast systems</p>
		<h1>Model families</h1>
		<p>Condensed entries linking to model-specific reference pages.</p>
	</header>

	<div class="table-list">
		{#each modelFamilies as model, index}
			<a href={`/almanac/models/${model.slug}`}>
				<span class="row-number">{String(index + 1).padStart(2, '0')}</span>
				<span class="primary">{model.name}</span>
				<span class="type">{model.modelType}</span>
				<span class="summary">{model.summary}</span>
				<span class="meta">{model.resolution}</span>
			</a>
		{/each}
	</div>
</main>

<style>
	.index-page {
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
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		align-items: flex-start;
		padding-bottom: 1.1rem;
	}

	.eyebrow {
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
		margin-bottom: 0;
		font-family: var(--font-display);
		font-size: clamp(1.5rem, 3vw, 1.85rem);
		font-weight: 550;
		line-height: 1.2;
		letter-spacing: -0.025em;
	}

	header p {
		max-width: 32rem;
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.92rem;
		line-height: 1.5;
	}

	.table-list {
		border-top: 0.0625rem solid var(--color-border);
	}

	.table-list a {
		display: grid;
		grid-template-columns: 2.2rem minmax(7rem, 10rem) minmax(8rem, 11rem) minmax(0, 1fr) minmax(
				7rem,
				9rem
			);
		gap: 0.7rem;
		align-items: baseline;
		border-bottom: 0.0625rem solid var(--color-border-subtle);
		color: var(--color-text);
		padding: 0.42rem 0;
		text-decoration: none;
	}

	.table-list a:hover .primary {
		color: var(--color-accent);
	}

	.row-number,
	.meta {
		color: var(--color-text-dim);
		font-family: var(--font-mono);
		font-size: 0.72rem;
	}

	.primary {
		font-size: 0.98rem;
		font-weight: 650;
	}

	.type {
		color: var(--color-accent);
		font-size: 0.68rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}

	.summary {
		color: var(--color-text-muted);
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 1;
		line-clamp: 1;
		overflow: hidden;
		font-size: 0.88rem;
		line-height: 1.4;
	}

	@media (max-width: 820px) {
		.index-page {
			width: min(100% - 2rem, 72rem);
		}

		.table-list a {
			grid-template-columns: 2.2rem minmax(0, 1fr);
		}

		.type,
		.summary,
		.meta {
			grid-column: 2;
		}
	}
</style>
