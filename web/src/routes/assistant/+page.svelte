<script lang="ts">
	import { page } from '$app/stores';
	import { account } from '$lib/account.svelte';
	import { getCapabilities } from '$lib/api';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import LoginPrompt from '$lib/LoginPrompt.svelte';

	// Each visit is a fresh conversation, as each setup page's is.
	const conversationKey = crypto.randomUUID();
	const initialPrompt = $page.url.searchParams.get('q')?.trim() ?? '';

	let chatAvailable = $state<boolean | null>(null);

	$effect(() => {
		if (!account.loaded || account.isAnonymous || chatAvailable !== null) return;
		getCapabilities()
			.then((capabilities) => (chatAvailable = capabilities.chat))
			.catch(() => (chatAvailable = false));
	});
</script>

<svelte:head><title>Assistant · AI Almanac</title></svelte:head>

<main class="assistant-page">
	{#if !account.loaded}
		<p class="muted">Loading…</p>
	{:else if account.isAnonymous}
		<LoginPrompt
			message={initialPrompt
				? 'Sign in to ask the assistant. Your question will be waiting when you get back.'
				: 'Sign in to ask the assistant.'}
		>
			<a class="secondary-action" href="/benchmarks">Browse the example results</a>
		</LoginPrompt>
	{:else if chatAvailable === null}
		<p class="muted">Loading…</p>
	{:else if chatAvailable}
		<ChatPanel
			jobs={[]}
			scopeKind="general"
			scopeKey={conversationKey}
			title="Assistant"
			emptyMessage="Ask about the models, regions and datasets, or describe what you want to find out. When you're ready, the assistant will take you to the right setup."
			placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
			suggestions={[
				'Which models can I benchmark for monsoon onset in India?',
				'What is the difference between a benchmark and a blend?',
				'How do you score onset forecasts?'
			]}
			initialMessage={initialPrompt}
		/>
	{:else}
		<section class="unavailable">
			<h1>The assistant is not available</h1>
			<p>
				It needs an AI provider. <a href="/settings/ai">Set up your AI provider</a>, or go straight
				to a setup:
			</p>
			<div class="links">
				<a href="/benchmarks?manual=1">Benchmark models</a>
				<a href="/blends?new=1">Blend models</a>
			</div>
		</section>
	{/if}
</main>

<style>
	.assistant-page {
		display: flex;
		flex-direction: column;
		width: min(100%, 56rem);
		height: calc(100dvh - 9rem);
		margin: 0 auto;
		padding: 1rem;
		box-sizing: border-box;
	}

	.muted {
		color: var(--color-text-muted);
	}

	.unavailable {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		margin: auto 0;
		text-align: center;
	}

	.unavailable h1 {
		margin: 0;
		font-size: 1.4rem;
	}

	.unavailable p {
		margin: 0;
		color: var(--color-text-muted);
	}

	.links {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 1rem;
	}
</style>
