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
		max-width: 32rem;
		margin-bottom: 0;
		color: #514c43;
		font-size: 0.9rem;
		font-weight: 600;
	}

	.table-list {
		border-top: 0.0625rem solid var(--almanac-rule);
		margin-top: 0.55rem;
	}

	.table-list a {
		display: grid;
		grid-template-columns: 2.2rem minmax(7rem, 10rem) minmax(6rem, 8rem) minmax(0, 1fr) minmax(7rem, 9rem);
		gap: 0.7rem;
		align-items: baseline;
		border-bottom: 0.0625rem solid var(--almanac-rule);
		color: var(--almanac-ink);
		padding: 0.36rem 0;
		text-decoration: none;
	}

	.table-list a:hover .primary {
		color: var(--almanac-red);
	}

	.row-number,
	.meta {
		color: #736c60;
		font-family: var(--font-mono);
		font-size: 0.7rem;
	}

	.primary {
		font-size: 0.98rem;
		font-weight: 850;
	}

	.type {
		color: var(--almanac-red);
		font-size: 0.62rem;
		font-weight: 800;
		letter-spacing: 0.12em;
		text-transform: uppercase;
	}

	.summary {
		color: #3d3932;
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 1;
		line-clamp: 1;
		overflow: hidden;
		font-size: 0.84rem;
		font-weight: 600;
		line-height: 1.32;
	}

	@media (max-width: 820px) {
		.index-page {
			width: min(100% - 2rem, 72rem);
		}

		header {
			grid-template-columns: 1fr;
			gap: 0.25rem;
		}

		header h1,
		header p {
			grid-column: auto;
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
