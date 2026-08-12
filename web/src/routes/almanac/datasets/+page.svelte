<script lang="ts">
	import { datasets } from '$lib/almanac/content';
</script>

<svelte:head>
	<title>Datasets | Almanac of Weather Models</title>
	<meta name="description" content="Condensed index of weather-model datasets." />
</svelte:head>

<main class="index-page">
	<a class="back-link" href="/almanac">Back to almanac</a>
	<header>
		<p class="eyebrow">Data lineage</p>
		<h1>Datasets</h1>
		<p>Training, validation, reanalysis, satellite, and benchmark observation datasets.</p>
	</header>

	<div class="table-list">
		{#each datasets as dataset, index}
			<a href={`/almanac/datasets/${dataset.slug}`}>
				<span class="row-number">{String(index + 1).padStart(2, '0')}</span>
				<span class="primary">{dataset.name}</span>
				<span class="type">{dataset.role}</span>
				<span class="summary">{dataset.summary}</span>
				<span class="meta">{dataset.coverage}</span>
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
		grid-template-columns: 2.2rem minmax(7rem, 10rem) minmax(6rem, 8rem) minmax(0, 1fr) minmax(
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
