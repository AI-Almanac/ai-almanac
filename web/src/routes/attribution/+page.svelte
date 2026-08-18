<script lang="ts">
	import { attributionSections } from '$lib/legal/attribution';
</script>

<svelte:head>
	<title>Attribution | AI Almanac</title>
	<meta
		name="description"
		content="Providers, licenses, and citations for the ground-truth datasets and forecast models used by AI Almanac."
	/>
</svelte:head>

<main class="attribution-page">
	<header>
		<p class="eyebrow">Credits and licenses</p>
		<h1>Model and dataset attribution</h1>
		<p>
			AI Almanac uses publicly available forecast models and observations by default. Every default
			source is listed below with its provider, corresponding citation, and the license that governs
			the artifact used on this site.
		</p>
	</header>

	{#each attributionSections as section}
		<section aria-labelledby={`${section.slug}-title`}>
			<div class="section-heading">
				<h2 id={`${section.slug}-title`}>{section.title}</h2>
				<p>{section.description}</p>
			</div>

			<div class="entry-list">
				{#each section.entries as entry}
					<article id={entry.slug}>
						<div class="entry-heading">
							<h3>{entry.name}</h3>
							<p class="provider">{entry.provider}</p>
						</div>
						<div class="entry-body">
							<p class="license">{entry.license}</p>
							<p class="usage">{entry.usage}</p>
							<ul class="citations">
								{#each entry.citations as citation}
									<li>{citation}</li>
								{/each}
							</ul>
							<p class="links">
								{#each entry.links as link, index}
									{#if index > 0}<span aria-hidden="true"> · </span>{/if}<a
										href={link.href}
										target="_blank"
										rel="noopener noreferrer">{link.label}</a
									>
								{/each}
							</p>
						</div>
					</article>
				{/each}
			</div>
		</section>
	{/each}

	<footer class="notes">
		<p>
			Most of the AI models above were trained on the ECMWF ERA5 reanalysis, distributed through the
			Copernicus Climate Change Service. Licenses can change upstream; the provider links are
			authoritative if they differ from this page.
		</p>
		<p>
			See <a href="/privacy">Data privacy</a> for how we handle user data, and the feedback link below
			to suggest improvements.
		</p>
	</footer>
</main>

<style>
	.attribution-page {
		width: min(100% - 3rem, 72rem);
		margin: 0 auto;
		padding: clamp(1rem, 3vw, 2rem) 0;
		color: var(--color-text);
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

	header p:last-child {
		max-width: 42rem;
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.92rem;
		line-height: 1.5;
	}

	section {
		display: grid;
		grid-template-columns: minmax(10rem, 13rem) minmax(0, 1fr);
		gap: 1rem;
		border-top: 0.0625rem solid var(--color-border);
		padding: 0.7rem 0;
	}

	h2 {
		margin-bottom: 0.3rem;
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}

	.section-heading p {
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.82rem;
		line-height: 1.45;
	}

	.entry-list {
		display: grid;
		border-top: 0.0625rem solid var(--color-border-subtle);
	}

	article {
		display: grid;
		grid-template-columns: minmax(10rem, 14rem) minmax(0, 1fr);
		gap: 0.8rem;
		border-bottom: 0.0625rem solid var(--color-border-subtle);
		padding: 0.65rem 0;
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

	.provider,
	.usage,
	.citations,
	.links {
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.88rem;
		line-height: 1.45;
	}

	.license {
		margin-bottom: 0.3rem;
		color: var(--color-text);
		font-size: 0.82rem;
		font-weight: 650;
		line-height: 1.4;
	}

	.usage {
		margin-bottom: 0.35rem;
	}

	.citations {
		padding-left: 0;
		list-style: none;
		font-size: 0.82rem;
	}

	.citations li {
		margin-bottom: 0.2rem;
		color: var(--color-text-dim);
	}

	.links {
		margin-top: 0.35rem;
		font-size: 0.8rem;
	}

	.links a,
	.notes a {
		color: var(--color-accent);
		text-decoration: underline;
		text-underline-offset: 0.2em;
	}

	.links a:hover,
	.notes a:hover {
		color: var(--color-accent-hover);
	}

	.links span {
		color: var(--color-text-dim);
	}

	.notes {
		border-top: 0.0625rem solid var(--color-border);
		padding-top: 0.9rem;
	}

	.notes p {
		max-width: 46rem;
		margin-bottom: 0.4rem;
		color: var(--color-text-muted);
		font-size: 0.82rem;
		line-height: 1.5;
	}

	.notes p:last-child {
		margin-bottom: 0;
	}

	@media (max-width: 860px) {
		.attribution-page {
			width: min(100% - 2rem, 72rem);
		}

		section,
		article {
			grid-template-columns: 1fr;
		}
	}
</style>
