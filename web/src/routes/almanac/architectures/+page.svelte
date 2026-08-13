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
	<meta
		name="description"
		content="Weather model architecture families and linked model families."
	/>
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
	h2,
	h3,
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
		max-width: 38rem;
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.92rem;
		line-height: 1.5;
	}

	.architecture-list {
		border-top: 0.0625rem solid var(--color-border);
	}

	section {
		border-bottom: 0.0625rem solid var(--color-border-subtle);
		padding: 0.62rem 0;
	}

	.entry-heading {
		display: grid;
		grid-template-columns: 2.2rem minmax(0, 1fr);
		gap: 0.7rem;
	}

	.row-number {
		color: var(--color-text-dim);
		font-family: var(--font-mono);
		font-size: 0.72rem;
	}

	h2 {
		margin-bottom: 0.25rem;
		font-family: var(--font-display);
		font-size: 1.05rem;
		font-weight: 650;
		line-height: 1.3;
		letter-spacing: -0.015em;
	}

	.entry-heading p {
		max-width: 48rem;
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.88rem;
		line-height: 1.45;
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
		color: var(--color-text-muted);
		font-size: 0.68rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}

	ul {
		margin: 0;
		padding-left: 1rem;
		color: var(--color-text-muted);
		font-size: 0.88rem;
		line-height: 1.45;
	}

	.model-links {
		padding-left: 0;
		list-style: none;
	}

	.model-links a {
		color: var(--color-accent);
		font-weight: 600;
		text-decoration-thickness: 0.0625rem;
		text-underline-offset: 0.16rem;
	}

	.model-links a:hover {
		color: var(--color-accent-hover);
	}

	@media (max-width: 820px) {
		.architecture-page {
			width: min(100% - 2rem, 72rem);
		}

		.entry-body {
			grid-template-columns: 1fr;
			padding-left: 0;
		}
	}
</style>
