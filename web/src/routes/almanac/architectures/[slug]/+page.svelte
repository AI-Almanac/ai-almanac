<script lang="ts">
	import BulletList from '$lib/almanac/BulletList.svelte';
	import TagList from '$lib/almanac/TagList.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let architecture = $derived(data.architecture);
</script>

<svelte:head>
	<title>{architecture.name} | Almanac of Weather Models</title>
	<meta name="description" content={architecture.summary} />
</svelte:head>

<main class="detail">
	<a class="back-link" href="/almanac#architectures">Back to architectures</a>
	<section class="detail-hero">
		<p class="eyebrow">Architecture</p>
		<h1>{architecture.name}</h1>
		<p class="lede">{architecture.summary}</p>
		<div class="ornament" aria-hidden="true">
			<span></span>
			<i></i>
			<span></span>
		</div>
	</section>

	<div class="detail-grid">
		<section aria-labelledby="families-title">
			<h2 id="families-title">Model Families</h2>
			<TagList items={architecture.modelFamilies} />
		</section>

		<section aria-labelledby="ideas-title">
			<h2 id="ideas-title">Key Ideas</h2>
			<TagList items={architecture.keyIdeas} />
		</section>

		<section aria-labelledby="notes-title">
			<h2 id="notes-title">Notes</h2>
			<BulletList items={architecture.notes} />
		</section>

		<section aria-labelledby="references-title">
			<h2 id="references-title">References</h2>
			{#if architecture.references.length}
				<ul class="references">
					{#each architecture.references as reference}
						<li><a href={reference.href}>{reference.label}</a></li>
					{/each}
				</ul>
			{:else}
				<p class="empty">To be filled</p>
			{/if}
		</section>
	</div>
</main>

<style>
	.detail {
		--almanac-rule: #bbb4a8;
		--almanac-ink: #2c2924;
		--almanac-red: #8b3f3d;
		width: min(100% - 3rem, 70rem);
		margin: 0 auto;
		padding: clamp(1.25rem, 4vw, 3rem) 0;
		color: var(--almanac-ink);
		font-family: var(--font-body);
	}

	.back-link {
		display: inline-flex;
		margin-bottom: 1.25rem;
		color: #736c60;
		font-size: 0.74rem;
		font-weight: 800;
		letter-spacing: 0.12em;
		text-decoration: none;
		text-transform: uppercase;
	}

	.back-link:hover {
		text-decoration: underline;
	}

	.detail-hero {
		max-width: 42rem;
		margin: 0 auto;
		border-bottom: 0.0625rem solid var(--almanac-rule);
		padding: clamp(0.75rem, 2.4vw, 1.4rem) 0 clamp(1.25rem, 3vw, 2.25rem);
		text-align: center;
	}

	.eyebrow {
		margin: 0 0 0.45rem;
		color: var(--almanac-red);
		font-size: 0.72rem;
		font-weight: 850;
		letter-spacing: 0.14em;
		text-transform: uppercase;
	}

	h1,
	h2,
	p {
		margin-top: 0;
	}

	h1 {
		margin-bottom: 0.65rem;
		font-size: clamp(2.25rem, 6vw, 4.4rem);
		font-weight: 800;
		line-height: 0.9;
		letter-spacing: 0;
	}

	.lede {
		max-width: 34rem;
		margin: 0 auto;
		color: #514c43;
		font-size: clamp(0.98rem, 1.6vw, 1.12rem);
		font-style: normal;
		font-weight: 600;
		line-height: 1.45;
	}

	.ornament {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 1rem;
		width: min(100%, 13rem);
		margin: 1rem auto 0;
		color: var(--almanac-rule);
	}

	.ornament span {
		flex: 1;
		border-top: 0.0625rem solid currentColor;
	}

	.ornament i {
		width: 0.52rem;
		aspect-ratio: 1;
		background: var(--almanac-ink);
		transform: rotate(45deg);
	}

	.detail-grid {
		display: grid;
		grid-template-columns: minmax(12rem, 17rem) minmax(0, 1fr);
		gap: clamp(1.25rem, 4vw, 3rem);
		padding-top: clamp(1.25rem, 3vw, 2.25rem);
	}

	section:not(.detail-hero) {
		border-top: 0.0625rem solid var(--almanac-rule);
		padding: 0.85rem 0 1rem;
	}

	section:not(.detail-hero):first-child {
		border-top: 0.14rem solid var(--almanac-ink);
	}

	h2 {
		margin-bottom: 0.65rem;
		font-size: 0.82rem;
		line-height: 1.2;
		letter-spacing: 0.12em;
		text-transform: uppercase;
	}

	.references {
		margin: 0;
		padding-left: 1.1rem;
	}

	.references {
		color: #514c43;
	}

	.empty {
		margin-bottom: 0;
		color: #736c60;
		font-style: italic;
	}

	@media (max-width: 760px) {
		.detail-grid {
			grid-template-columns: 1fr;
		}

		.detail {
			width: min(100% - 2rem, 70rem);
		}
	}
</style>
