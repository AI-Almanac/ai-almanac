<script lang="ts">
	import { goto } from '$app/navigation';

	const examples = [
		'Compare monsoon onset skill over southern India',
		'Benchmark Kiremt onset forecasts in Ethiopia',
		'Find models with fewer missed onsets at long lead times'
	];

	const workflows = [
		{
			id: 'benchmark',
			title: 'Benchmark models',
			description: 'Compare forecast models against ground truth observations.',
			href: '/benchmarks?manual=1'
		}
	];

	let prompt = '';
	let workflowLauncherOpen = false;

	function closeOnEscape(event: KeyboardEvent) {
		if (workflowLauncherOpen && event.key === 'Escape') workflowLauncherOpen = false;
	}

	async function startWorkflow(href: string) {
		workflowLauncherOpen = false;
		await goto(href);
	}
</script>

<svelte:window onkeydown={closeOnEscape} />

<main class="landing">
	<section class="search-hero">
		<h1>AI Almanac</h1>
		<p class="lede">Human-centric Climate Insights using the latest AI Weather Prediction Models</p>

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

		<div class="start-row">
			<button
				class="get-started"
				type="button"
				aria-haspopup="dialog"
				onclick={() => (workflowLauncherOpen = true)}
			>
				Get started
			</button>
		</div>
	</section>
</main>

{#if workflowLauncherOpen}
	<div class="workflow-layer" role="presentation">
		<button
			class="workflow-scrim"
			type="button"
			aria-label="Close workflow chooser"
			onclick={() => (workflowLauncherOpen = false)}
		></button>
		<div class="workflow-panel" role="dialog" aria-modal="true" aria-labelledby="workflow-title">
			<header class="workflow-header">
				<h2 id="workflow-title">Get started</h2>
				<button
					class="workflow-close"
					type="button"
					aria-label="Close workflow chooser"
					onclick={() => (workflowLauncherOpen = false)}>×</button
				>
			</header>

			<div class="workflow-list">
				{#each workflows as workflow}
					<button class="workflow-card" type="button" onclick={() => startWorkflow(workflow.href)}>
						<span>{workflow.title}</span>
						<small>{workflow.description}</small>
					</button>
				{/each}
			</div>
		</div>
	</div>
{/if}

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
		padding: 0.85rem 1.25rem;
		font-weight: 800;
		cursor: pointer;
	}

	.search-box button[type='submit'] {
		background: var(--color-accent);
		color: white;
	}

	.start-row {
		display: flex;
		justify-content: center;
		margin-top: 1.25rem;
	}

	.get-started {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		background: var(--color-bg);
		color: var(--color-text);
		padding: 0.68rem 1.15rem;
		font: inherit;
		font-size: 0.94rem;
		font-weight: 800;
		white-space: nowrap;
		cursor: pointer;
	}

	.get-started:hover {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
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

	.workflow-layer {
		position: fixed;
		inset: 0;
		z-index: 80;
		display: grid;
		place-items: center;
		padding: 1rem;
	}

	.workflow-scrim {
		position: absolute;
		inset: 0;
		border: 0;
		background: rgba(30, 27, 23, 0.26);
		cursor: pointer;
	}

	.workflow-panel {
		position: relative;
		width: min(100%, 28rem);
		border: 1px solid var(--color-border);
		border-radius: 0.6rem;
		background: var(--color-surface-raised);
		box-shadow: 0 1rem 3rem rgba(36, 33, 29, 0.16);
		overflow: hidden;
		text-align: left;
	}

	.workflow-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		padding: 1rem 1.1rem;
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.workflow-header h2 {
		margin: 0;
		font-size: 1.35rem;
		line-height: 1.1;
		color: var(--color-text);
	}

	.workflow-close {
		width: 2rem;
		aspect-ratio: 1;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-bg);
		color: var(--color-text);
		font-size: 1.35rem;
		line-height: 1;
		cursor: pointer;
	}

	.workflow-list {
		display: flex;
		flex-direction: column;
		padding: 0.65rem;
	}

	.workflow-card {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		width: 100%;
		border: 0;
		border-radius: 0.45rem;
		background: transparent;
		padding: 0.85rem;
		color: var(--color-text);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}

	.workflow-card:hover {
		background: var(--color-accent-light);
	}

	.workflow-card span {
		font-weight: 850;
	}

	.workflow-card small {
		color: var(--color-text-muted);
		font-size: 0.86rem;
		line-height: 1.4;
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
