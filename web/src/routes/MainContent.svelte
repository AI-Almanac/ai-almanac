<script>
	const examples = [
		'Compare monsoon onset skill over southern India',
		'Benchmark Kiremt onset forecasts in Ethiopia',
		'Find models with fewer missed onsets at long lead times'
	];

	let prompt = '';
</script>

<main class="landing">
	<section class="search-hero">
		<h1>AI Almanac</h1>
		<p class="lede">
			Human-centric Climate Insights using the latest AI Weather Prediction Models
		</p>

		<form class="search-box" action="/benchmarks" method="GET">
			<input
				name="q"
				bind:value={prompt}
				placeholder="Which model performs best for monsoon onset over India?"
				aria-label="Benchmark question"
			/>
			<button type="submit" disabled={!prompt.trim()}>Ask</button>
		</form>

		<div class="examples" aria-label="Example prompts">
			{#each examples as example}
				<button type="button" onclick={() => (prompt = example)}>{example}</button>
			{/each}
		</div>
	</section>
</main>

<style>
	.landing {
		width: min(100% - 2rem, 58rem);
		min-height: calc(100vh - 4rem);
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		justify-content: center;
		gap: clamp(2rem, 6vw, 4rem);
		padding: clamp(2rem, 8vw, 6rem) 0;
	}

	.search-hero {
		text-align: center;
	}

	h1 {
		margin: 0;
		font-size: clamp(2.75rem, 7vw, 5.5rem);
		line-height: 1;
		letter-spacing: 0;
	}

	.lede {
		max-width: 42rem;
		margin: 1.1rem auto 0;
		color: var(--color-text-muted);
		font-size: clamp(1rem, 2vw, 1.16rem);
	}

	.search-box {
		width: min(100%, 48rem);
		display: flex;
		align-items: center;
		gap: 0.65rem;
		margin: clamp(1.5rem, 4vw, 2.5rem) auto 0;
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		background: var(--color-surface);
		box-shadow: 0 0.8rem 2.5rem rgba(36, 33, 29, 0.06);
		padding: 0.5rem;
	}

	.search-box input {
		flex: 1;
		min-width: 0;
		border: 0;
		outline: 0;
		background: transparent;
		color: var(--color-text);
		padding: 0.85rem 1rem;
		font-size: 1rem;
	}

	.search-box button {
		border: 0;
		border-radius: 999rem;
		background: var(--color-accent);
		color: white;
		padding: 0.85rem 1.25rem;
		font-weight: 800;
		cursor: pointer;
	}

	.search-box button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}

	.examples {
		display: flex;
		justify-content: center;
		flex-wrap: wrap;
		gap: 0.55rem;
		margin-top: 1rem;
	}

	.examples button {
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		background: transparent;
		color: var(--color-text-muted);
		padding: 0.5rem 0.8rem;
		font-size: 0.9rem;
		cursor: pointer;
	}

	.examples button:hover {
		border-color: var(--color-accent);
		color: var(--color-accent);
	}

	@media (max-width: 720px) {
		.search-box {
			border-radius: 1rem;
			align-items: stretch;
			flex-direction: column;
		}

		.search-box button {
			width: 100%;
		}

		:global(.promise) {
			grid-template-columns: 1fr;
		}
	}
</style>
