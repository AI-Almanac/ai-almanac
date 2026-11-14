<script lang="ts">
	import type { Dataset, GuardrailThresholds, Region, RompDefaults } from '$lib/api';
	import type { BenchmarkStore } from '$lib/benchmarks.svelte';
	import { goToBlend } from '$lib/blend-nav';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import BenchmarkForm from '$lib/components/benchmark-setup/BenchmarkForm.svelte';
	import { BenchmarkSetupForm } from '$lib/components/benchmark-setup/setup-form.svelte';
	import { benchmarkSetupSteps } from '$lib/components/benchmark-setup/tours';
	import { installTour } from '$lib/tour.svelte';

	interface Props {
		store: BenchmarkStore;
		regions: Region[];
		datasets: Dataset[];
		dataLoaded: boolean;
		parameterDefaults: RompDefaults | null;
		guardrails: GuardrailThresholds | null;
		/** What the user asked for before the page opened, sent to the assistant first. */
		initialPrompt?: string;
		/** Open the full benchmark settings straight away. */
		initialManualOpen?: boolean;
		chatAvailable?: boolean;
		onSubmitted: (groupKey: string, chatSessionId?: string | null) => void;
	}

	const {
		store,
		regions,
		datasets,
		dataLoaded,
		parameterDefaults,
		guardrails,
		initialPrompt = '',
		initialManualOpen = false,
		chatAvailable = false,
		onSubmitted
	}: Props = $props();

	const setupKey = crypto.randomUUID();
	const form = new BenchmarkSetupForm(
		() => store,
		(runId, sessionId) => onSubmitted(runId, sessionId)
	);

	let settingsOpen = $state(false);
	$effect(() => {
		if (initialManualOpen) settingsOpen = true;
	});
	installTour('benchmark-setup', () => benchmarkSetupSteps(() => (settingsOpen = true)));

	// Edits made in the settings reach the assistant's copy of the plan.
	$effect(() => {
		if (!form.chatSessionId || !form.manualConfigDirty) return;
		const timer = setTimeout(() => {
			void form.syncBenchmarkConfig({ showErrors: false });
		}, 500);
		return () => clearTimeout(timer);
	});

	// A comparison needs a wider assistant column than the plan panel leaves.
	let chatComparing = $state(false);
</script>

<section class="setup-workspace" class:is-comparing={chatComparing}>
	<div class="setup-chat" data-tour="setup-chat">
		{#if chatAvailable}
			<ChatPanel
				onComparingChange={(value) => (chatComparing = value)}
				jobs={[]}
				scopeKind="benchmark_setup"
				scopeKey={setupKey}
				title="Benchmark setup"
				emptyMessage="Describe the benchmark you want, ask what the options mean, or start from one of the examples."
				placeholder="Ask for the benchmark you want, or ask a question…"
				suggestions={[
					'Compare monsoon onset skill over southern India',
					'Benchmark Kiremt onset forecasts in Ethiopia',
					'What does climatology mean in this context?'
				]}
				initialMessage={initialPrompt}
				onSessionReady={form.handleSessionReady}
				onBenchmarkConfig={form.applySpec}
				onBenchmarkSubmitted={form.handleBenchmarkSubmitted}
				onBlendSubmitted={(_runId, jobs, sessionId) =>
					goToBlend(jobs[0]?.id, sessionId, { kind: 'benchmark_setup', key: setupKey })}
			/>
		{:else}
			<div class="manual-setup">
				<p class="eyebrow">Benchmark setup</p>
				<h1>Configure a benchmark</h1>
				<p>
					The AI assistant is unavailable until an LLM is set up. Use the benchmark settings to
					select observations, models, and benchmark parameters, or
					<a href="/settings/ai">set up your AI provider</a>.
				</p>
				<button type="button" onclick={() => (settingsOpen = true)}>
					Open benchmark settings
				</button>
			</div>
		{/if}
	</div>

	<div class="setup-plan">
		<BenchmarkForm
			{form}
			{regions}
			{datasets}
			{dataLoaded}
			{parameterDefaults}
			{guardrails}
			{chatAvailable}
			bind:settingsOpen
		/>
	</div>
</section>

<style>
	.setup-workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(22rem, 30rem);
		gap: clamp(1rem, 3vw, 2rem);
		height: var(--workspace-screen-height);
	}

	/* Two answers side by side need the room the plan panel is using. */
	.setup-workspace.is-comparing {
		grid-template-columns: minmax(0, 1fr) minmax(16rem, 20rem);
	}

	.setup-chat {
		min-width: 0;
		min-height: 0;
		display: flex;
	}

	.setup-chat :global(.chat-panel) {
		min-height: 100%;
		box-shadow: var(--shadow-soft);
	}

	.setup-plan {
		display: flex;
		flex-direction: column;
		min-height: 0;
		border-radius: 0.5rem;
		box-shadow: var(--shadow-soft);
	}

	.manual-setup {
		width: 100%;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		justify-content: center;
		gap: 1rem;
		padding: clamp(1.5rem, 5vw, 4rem);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
	}

	.manual-setup h1,
	.manual-setup p {
		margin: 0;
	}

	.manual-setup p:not(.eyebrow) {
		max-width: 38rem;
		color: var(--color-text-muted);
	}

	.eyebrow {
		color: var(--color-accent);
		font-size: 0.78rem;
		font-weight: 750;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}

	.manual-setup button {
		border: 0;
		border-radius: 0.45rem;
		background: var(--color-accent);
		color: white;
		padding: 0.75rem 1rem;
		font: inherit;
		font-weight: 750;
		cursor: pointer;
	}

	@media (max-width: 54rem) {
		.setup-workspace {
			grid-template-columns: 1fr;
			height: auto;
		}

		.setup-chat {
			height: var(--workspace-screen-height);
		}
	}
</style>
