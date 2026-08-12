<script lang="ts" module>
	export function value(text: string): string {
		return text.trim() || 'To be filled';
	}
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import BulletList from '$lib/almanac/BulletList.svelte';

	interface Props {
		backHref: string;
		backLabel: string;
		eyebrow: string;
		title: string;
		summary: string;
		notes: string[];
		references: { href: string; label: string }[];
		sections?: Snippet;
	}

	const { backHref, backLabel, eyebrow, title, summary, notes, references, sections }: Props =
		$props();
</script>

<svelte:head>
	<title>{title} | Almanac of Weather Models</title>
	<meta name="description" content={summary} />
</svelte:head>

<main class="detail">
	<a class="back-link" href={backHref}>{backLabel}</a>
	<section class="detail-hero">
		<p class="eyebrow">{eyebrow}</p>
		<h1>{title}</h1>
		<p class="lede">{summary}</p>
		<div class="ornament" aria-hidden="true">
			<span></span>
			<i></i>
			<span></span>
		</div>
	</section>

	<div class="detail-grid">
		{@render sections?.()}

		<section aria-labelledby="notes-title">
			<h2 id="notes-title">Notes</h2>
			<BulletList items={notes} />
		</section>

		<section aria-labelledby="references-title">
			<h2 id="references-title">References</h2>
			{#if references.length}
				<ul class="references">
					{#each references as reference}
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
		width: min(100% - 3rem, 70rem);
		margin: 0 auto;
		padding: clamp(1.25rem, 4vw, 3rem) 0;
		color: var(--color-text);
		font-family: var(--font-body);
	}

	.back-link {
		display: inline-flex;
		margin-bottom: 1.25rem;
		color: var(--color-text-muted);
		font-size: 0.8rem;
		font-weight: 650;
		text-decoration: none;
	}

	.back-link:hover {
		color: var(--color-text);
		text-decoration: underline;
	}

	.detail-hero {
		max-width: 42rem;
		margin: 0 auto;
		border-bottom: 0.0625rem solid var(--color-border);
		padding: clamp(0.75rem, 2.4vw, 1.4rem) 0 clamp(1.25rem, 3vw, 2.25rem);
		text-align: center;
	}

	.eyebrow {
		margin: 0 0 0.45rem;
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}

	.detail :global(h1),
	.detail :global(h2),
	.detail :global(h3),
	.detail :global(p) {
		margin-top: 0;
	}

	h1 {
		margin-bottom: 0.65rem;
		font-family: var(--font-display);
		font-size: clamp(1.75rem, 4vw, 2.4rem);
		font-weight: 550;
		line-height: 1.2;
		letter-spacing: -0.025em;
	}

	.lede {
		max-width: 34rem;
		margin: 0 auto;
		color: var(--color-text-muted);
		font-size: clamp(0.98rem, 1.6vw, 1.08rem);
		line-height: 1.5;
	}

	.ornament {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 1rem;
		width: min(100%, 13rem);
		margin: 1rem auto 0;
		color: var(--color-border);
	}

	.ornament span {
		flex: 1;
		border-top: 0.0625rem solid currentColor;
	}

	.ornament i {
		width: 0.5rem;
		aspect-ratio: 1;
		background: var(--color-accent);
		transform: rotate(45deg);
	}

	.detail-grid {
		display: grid;
		grid-template-columns: minmax(12rem, 17rem) minmax(0, 1fr);
		gap: clamp(1.25rem, 4vw, 3rem);
		padding-top: clamp(1.25rem, 3vw, 2.25rem);
	}

	.detail-grid :global(section) {
		border-top: 0.0625rem solid var(--color-border);
		padding: 0.85rem 0 1rem;
	}

	.detail-grid :global(section:first-child) {
		border-top: 0.14rem solid var(--color-accent);
	}

	.detail-grid :global(h2) {
		margin-bottom: 0.65rem;
		color: var(--color-text-muted);
		font-size: 0.72rem;
		font-weight: 750;
		line-height: 1.2;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}

	.detail-grid :global(h3) {
		margin-bottom: 0.45rem;
		color: var(--color-accent);
		font-size: 0.88rem;
		font-weight: 650;
	}

	.detail-grid :global(dl),
	.references {
		margin: 0;
	}

	.detail-grid :global(dl) {
		display: grid;
		flex-direction: column;
		gap: 0;
		border-top: 0.0625rem solid var(--color-border-subtle);
	}

	.detail-grid :global(dl div) {
		display: grid;
		grid-template-columns: minmax(6rem, 8.5rem) minmax(0, 1fr);
		border-bottom: 0.0625rem solid var(--color-border-subtle);
	}

	.detail-grid :global(dt) {
		color: var(--color-text-muted);
		font-size: 0.68rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		padding: 0.36rem 0.55rem 0.36rem 0;
		text-transform: uppercase;
	}

	.detail-grid :global(dd) {
		border-left: 0.0625rem solid var(--color-border-subtle);
		margin: 0;
		padding: 0.36rem 0 0.36rem 0.65rem;
		font-size: 0.9rem;
		font-weight: 600;
	}

	.references {
		padding-left: 1.1rem;
		color: var(--color-text-muted);
	}

	.references a {
		color: var(--color-accent);
	}

	.references a:hover {
		color: var(--color-accent-hover);
	}

	.detail-grid :global(.split-list) {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
	}

	.detail-grid :global(.split-list > div) {
		flex: 1 1 12rem;
	}

	.empty {
		margin-bottom: 0;
		color: var(--color-text-dim);
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
