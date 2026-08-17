<script lang="ts">
	const points = [
		{
			title: 'Your data stays yours',
			body: 'Data you upload or register as a data source is not shared with third parties and is not sold, licensed, or used to train models.'
		},
		{
			title: 'Not visible to us',
			body: 'AI Almanac developers cannot see the contents of user-provided data. It is scoped to your account and used only to run the benchmarks and forecasts you ask for.'
		},
		{
			title: 'Defaults are public',
			body: 'Every dataset and model shipped as a platform default is either publicly available or reproducible by running a publicly available model on public initial conditions. Providers, licenses, and citations are listed on the attribution page.'
		}
	];
</script>

<svelte:head>
	<title>Data privacy | AI Almanac</title>
	<meta
		name="description"
		content="How AI Almanac handles user-provided data, and where its default data comes from."
	/>
</svelte:head>

<main class="privacy-page">
	<header>
		<p class="eyebrow">Policy</p>
		<h1>Data privacy</h1>
		<p>The short version, which is also the whole version.</p>
	</header>

	<div class="points">
		{#each points as point}
			<section>
				<h2>{point.title}</h2>
				<p>{point.body}</p>
			</section>
		{/each}
	</div>

	<p class="footnote">
		See <a href="/attribution">model and dataset attribution</a> for the provenance of the default data.
	</p>
</main>

<style>
	.privacy-page {
		width: min(100% - 3rem, 48rem);
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
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.92rem;
		line-height: 1.5;
	}

	.points {
		border-top: 0.0625rem solid var(--color-border);
	}

	section {
		display: grid;
		grid-template-columns: minmax(9rem, 12rem) minmax(0, 1fr);
		gap: 1rem;
		border-bottom: 0.0625rem solid var(--color-border);
		padding: 0.8rem 0;
	}

	h2 {
		margin-bottom: 0;
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 650;
		line-height: 1.3;
		letter-spacing: -0.015em;
	}

	section p {
		margin-bottom: 0;
		color: var(--color-text-muted);
		font-size: 0.9rem;
		line-height: 1.5;
	}

	.footnote {
		margin: 0.9rem 0 0;
		color: var(--color-text-dim);
		font-size: 0.82rem;
		line-height: 1.5;
	}

	.footnote a {
		color: var(--color-accent);
		text-decoration: underline;
		text-underline-offset: 0.2em;
	}

	.footnote a:hover {
		color: var(--color-accent-hover);
	}

	@media (max-width: 720px) {
		.privacy-page {
			width: min(100% - 2rem, 48rem);
		}

		section {
			grid-template-columns: 1fr;
			gap: 0.3rem;
		}
	}
</style>
