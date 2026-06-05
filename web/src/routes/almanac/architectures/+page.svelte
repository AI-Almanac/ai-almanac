<script lang="ts">
	import { architectures, modelFamilies } from '$lib/almanac/content';

	function modelsForArchitecture(names: string[]) {
		return names
			.map((name) => modelFamilies.find((model) => model.name.toLowerCase() === name.toLowerCase()))
			.filter((model) => model !== undefined);
	}
</script>

<svelte:head>
	<title>Architectures | Almanac of Weather Models</title>
	<meta name="description" content="Weather model architecture families and linked model families." />
</svelte:head>

<main class="architecture-page">
	<a class="back-link" href="/almanac">Back to almanac</a>
	<header>
		<p class="eyebrow">Model design</p>
		<h1>Architectures</h1>
		<p>
			A short field guide to model architecture patterns. These entries describe broad modeling
			approaches and link back to the model families that use them.
		</p>
	</header>

	<div class="architecture-list">
		{#each architectures as architecture, index}
			<section aria-labelledby={`${architecture.slug}-title`}>
				<div class="entry-heading">
					<span class="row-number">{String(index + 1).padStart(2, '0')}</span>
					<div>
						<h2 id={`${architecture.slug}-title`}>{architecture.name}</h2>
						<p>{architecture.summary}</p>
					</div>
				</div>
				<div class="entry-body">
					<div>
						<h3>Key ideas</h3>
						<ul>
							{#each architecture.keyIdeas as idea}
								<li>{idea}</li>
							{/each}
						</ul>
					</div>
					<div>
						<h3>Models</h3>
						<ul class="model-links">
							{#each modelsForArchitecture(architecture.modelFamilies) as model}
								<li><a href={`/almanac/models/${model.slug}`}>{model.name}</a></li>
							{/each}
						</ul>
					</div>
				</div>
			</section>
		{/each}
	</div>
</main>

<style>
	.architecture-page {
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
		max-width: 38rem;
		margin-bottom: 0;
		color: #514c43;
		font-size: 0.9rem;
		font-weight: 600;
	}

	.architecture-list {
		border-top: 0.0625rem solid var(--almanac-rule);
		margin-top: 0.55rem;
	}

	section {
		border-bottom: 0.0625rem solid var(--almanac-rule);
		padding: 0.62rem 0;
	}

	.entry-heading {
		display: grid;
		grid-template-columns: 2.2rem minmax(0, 1fr);
		gap: 0.7rem;
	}

	.row-number {
		color: #736c60;
		font-family: var(--font-mono);
		font-size: 0.7rem;
	}

	h2 {
		margin-bottom: 0.25rem;
		font-size: 1.05rem;
		line-height: 1.1;
	}

	.entry-heading p {
		max-width: 48rem;
		margin-bottom: 0;
		color: #3d3932;
		font-size: 0.84rem;
		font-weight: 600;
		line-height: 1.35;
	}

	.entry-body {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(10rem, 14rem);
		gap: 1rem;
		margin-top: 0.48rem;
		padding-left: 2.9rem;
	}

	h3 {
		margin-bottom: 0.22rem;
		color: var(--almanac-red);
		font-size: 0.62rem;
		font-weight: 800;
		letter-spacing: 0.12em;
		text-transform: uppercase;
	}

	ul {
		margin: 0;
		padding-left: 1rem;
		color: #514c43;
		font-size: 0.84rem;
		font-weight: 600;
		line-height: 1.3;
	}

	.model-links {
		padding-left: 0;
		list-style: none;
	}

	.model-links a {
		color: var(--almanac-ink);
		font-weight: 800;
		text-decoration-color: var(--almanac-red);
		text-decoration-thickness: 0.08rem;
		text-underline-offset: 0.16rem;
	}

	.model-links a:hover {
		color: var(--almanac-red);
	}

	@media (max-width: 820px) {
		.architecture-page {
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

		.entry-body {
			grid-template-columns: 1fr;
			padding-left: 0;
		}
	}
</style>
