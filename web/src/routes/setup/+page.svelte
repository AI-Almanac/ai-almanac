<script lang="ts">
	import { browser } from '$app/environment';
	import { storeSetupToken, getSetupToken } from '$lib/api/setup';
	import { SetupWizardState } from '$lib/setup/wizard.svelte';
	import SystemStep from './SystemStep.svelte';
	import StorageStep from './StorageStep.svelte';
	import LlmStep from './LlmStep.svelte';
	import EnvPrepareStep from './EnvPrepareStep.svelte';
	import FinishStep from './FinishStep.svelte';

	const wizard = new SetupWizardState();

	if (browser) {
		// Capture token from URL, strip it from the address bar, then load state
		const params = new URLSearchParams(window.location.search);
		const urlToken = params.get('token');
		if (urlToken) {
			storeSetupToken(urlToken);
			const clean = new URL(window.location.href);
			clean.searchParams.delete('token');
			history.replaceState(null, '', clean.toString());
		}
		void wizard.load();
	}

	const hasToken = $derived(browser ? Boolean(getSetupToken()) : false);

	const STEP_LABELS = [
		{ id: 'system', label: 'System' },
		{ id: 'storage', label: 'Storage' },
		{ id: 'llm', label: 'LLM' },
		{ id: 'envs', label: 'Environments' },
		{ id: 'finish', label: 'Finish' }
	];
</script>

<svelte:head>
	<title>First-run setup — AI Almanac</title>
</svelte:head>

<div class="setup-shell">
	<header class="setup-header">
		<h1>First-run setup</h1>
		<p class="subtitle">Configure AI Almanac for local use</p>
	</header>

	{#if !hasToken}
		<div class="no-token-card">
			<h2>Setup URL required</h2>
			<p>
				Open the URL printed in the terminal when you ran <code>ai-almanac serve</code>. It looks
				like:
			</p>
			<pre>http://localhost:8765/setup?token=…</pre>
			<p>The token is stored in your browser session once you open that link.</p>
		</div>
	{:else}
		<nav class="step-nav">
			{#each STEP_LABELS as s, i (s.id)}
				{@const active = wizard.step === s.id}
				{@const pastIdx = STEP_LABELS.findIndex((x) => x.id === wizard.step)}
				<span class="step-pill" class:active class:done={i < pastIdx}>
					<span class="pill-num">{i + 1}</span>
					{s.label}
				</span>
			{/each}
		</nav>

		{#if wizard.loading}
			<div class="loading">Loading…</div>
		{:else if wizard.error}
			<div class="error-card">
				<strong>Could not load setup state</strong>
				<p>{wizard.error}</p>
				<button onclick={() => void wizard.load()}>Retry</button>
			</div>
		{:else if wizard.step === 'system'}
			<SystemStep {wizard} />
		{:else if wizard.step === 'storage'}
			<StorageStep {wizard} />
		{:else if wizard.step === 'llm'}
			<LlmStep {wizard} />
		{:else if wizard.step === 'envs'}
			<EnvPrepareStep {wizard} />
		{:else if wizard.step === 'finish'}
			<FinishStep {wizard} />
		{/if}
	{/if}
</div>

<style>
	.setup-shell {
		max-width: 640px;
		margin: 2.5rem auto;
		padding: 0 1rem;
		display: flex;
		flex-direction: column;
		gap: 1.5rem;
	}
	.setup-header {
		text-align: center;
	}
	.setup-header h1 {
		margin: 0;
		font-size: 1.6rem;
	}
	.subtitle {
		margin: 0.25rem 0 0;
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.step-nav {
		display: flex;
		gap: 0.5rem;
		justify-content: center;
		flex-wrap: wrap;
	}
	.step-pill {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.3rem 0.75rem;
		border-radius: 999px;
		font-size: 0.8rem;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		color: var(--color-text-muted);
	}
	.step-pill.active {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: #fff;
		font-weight: 600;
	}
	.step-pill.done {
		color: var(--color-accent);
		border-color: var(--color-accent);
	}
	.pill-num {
		font-size: 0.68rem;
		font-weight: 700;
		background: currentColor;
		color: var(--color-surface);
		width: 1.1em;
		height: 1.1em;
		border-radius: 50%;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.step-pill.active .pill-num {
		background: rgba(255, 255, 255, 0.3);
		color: #fff;
	}
	.loading {
		text-align: center;
		color: var(--color-text-muted);
		padding: 2rem;
	}
	.error-card {
		padding: 1.25rem;
		border: 1px solid var(--color-status-failed);
		border-radius: 0.6rem;
		background: var(--color-status-failed-bg, #fee);
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.error-card p {
		margin: 0;
		font-size: 0.85rem;
	}
	.no-token-card {
		padding: 1.5rem;
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
	}
	.no-token-card h2 {
		margin: 0 0 0.75rem;
	}
	.no-token-card p {
		margin: 0 0 0.5rem;
		font-size: 0.9rem;
		color: var(--color-text-muted);
	}
	pre {
		margin: 0.5rem 0;
		padding: 0.6rem 0.85rem;
		border-radius: 0.4rem;
		background: var(--color-surface);
		font-size: 0.8rem;
		overflow-x: auto;
	}
</style>
