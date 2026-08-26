<script lang="ts">
	import { type BenchmarkStore } from '$lib/benchmarks.svelte';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import { goToBlend } from '$lib/blend-nav';
	import type { Dataset, Region, RompDefaults } from '$lib/api';
	import AdvancedRompConfigPanel from './AdvancedRompConfigPanel.svelte';
	import { BenchmarkSetupForm } from './setup-form.svelte';

	let {
		store,
		regions,
		datasets,
		dataLoaded,
		parameterDefaults,
		initialPrompt = '',
		initialManualOpen = false,
		chatAvailable = false,
		onSubmitted
	}: {
		store: BenchmarkStore;
		regions: Region[];
		datasets: Dataset[];
		dataLoaded: boolean;
		parameterDefaults: RompDefaults | null;
		initialPrompt?: string;
		initialManualOpen?: boolean;
		chatAvailable?: boolean;
		onSubmitted: (groupKey: string, chatSessionId?: string | null) => void;
	} = $props();

	const setupKey = crypto.randomUUID();
	const form = new BenchmarkSetupForm(
		() => store,
		(runId, sessionId) => onSubmitted(runId, sessionId)
	);

	let advancedPanelOpen = $state(false);
	let initialManualOpenHandled = $state(false);

	$effect(() => {
		form.regions = regions;
		form.datasets = datasets;
		form.parameterDefaults = parameterDefaults;
		form.dataLoaded = dataLoaded;
	});

	$effect(() => {
		if (!initialManualOpen || initialManualOpenHandled) return;
		initialManualOpenHandled = true;
		advancedPanelOpen = true;
	});

	$effect(() => {
		if (!form.chatSessionId || !form.manualConfigDirty) return;
		const timer = setTimeout(() => {
			void form.syncBenchmarkConfig({ showErrors: false });
		}, 500);
		return () => clearTimeout(timer);
	});

	type ConfigSection = 'plan' | 'models';
	let panelFocusSection = $state<ConfigSection | null>(null);

	function openConfig(section: ConfigSection | null = null) {
		panelFocusSection = section;
		advancedPanelOpen = true;
	}

	const specSlots = $derived([
		{
			label: 'Ground truth',
			value: form.selectedDataset?.name ?? form.spec?.dataset_name ?? null,
			section: 'plan' as const
		},
		{
			label: 'Region',
			value: form.selectedRegion?.display_name ?? form.spec?.region_name ?? null,
			section: 'plan' as const
		},
		{
			label: 'Models',
			value:
				form.selectedModels.length > 0
					? form.selectedModels.map((model) => model.display_name).join(', ')
					: (form.spec?.model_names.join(', ') || null),
			section: 'models' as const
		},
		{
			label: 'Forecast window',
			value: form.forecastWindowDays ? `Days 1-${form.forecastWindowDays}` : 'All days',
			section: null
		}
	]);

	const missingSteps = $derived(
		[
			!form.selectedDatasetId && 'a ground-truth dataset',
			!form.selectedRegionId && 'a region',
			form.selectedModelIds.length === 0 && 'at least one model'
		].filter((step): step is string => Boolean(step))
	);

	function listPhrase(items: string[]): string {
		if (items.length <= 1) return items[0] ?? '';
		return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
	}

	function closeManualConfig() {
		advancedPanelOpen = false;
		panelFocusSection = null;
		void form.syncBenchmarkConfig({ showErrors: true });
	}

	// A comparison needs a wider assistant column than the plan panel leaves.
	let chatComparing = $state(false);
</script>

<section class="setup-workspace has-plan" class:is-comparing={chatComparing}>
	<div class="setup-chat">
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
				showArtifacts={false}
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
				<button type="button" onclick={() => (advancedPanelOpen = true)}>
					Open benchmark settings
				</button>
			</div>
		{/if}
	</div>

	<aside class="review-panel">
		<div class="review-header">
			<div class="state-row">
				<p class="eyebrow">Current setup</p>
				<span class="state-pill" class:runnable={form.runState === 'runnable'}>
					{form.runState === 'runnable'
						? 'Ready to run'
						: form.runState === 'running'
							? 'Starting'
							: missingSteps.length > 0
								? `${missingSteps.length} step${missingSteps.length === 1 ? '' : 's'} left`
								: 'Needs info'}
				</span>
			</div>
			<h2>Benchmark plan</h2>
			<p>
				{#if chatAvailable}
					Describe the benchmark you want in the chat and the assistant will fill this plan in —
					or configure it yourself.
				{:else}
					Fill in the plan with the benchmark settings below.
				{/if}
			</p>
		</div>

		<div class="spec-list">
			{#each specSlots as slot}
				<div>
					<span>{slot.label}</span>
					{#if slot.value !== null}
						<strong>{slot.value}</strong>
					{:else}
						<button class="slot-action" type="button" onclick={() => openConfig(slot.section)}>
							Choose →
						</button>
					{/if}
				</div>
			{/each}
		</div>

		<button class="advanced-button" type="button" onclick={() => openConfig()}>
			<span>Configure it yourself</span>
			<small>
				{#if form.syncingConfig}
					Validating...
				{:else}
					{form.selectedModelIds.length} selected model{form.selectedModelIds.length === 1
						? ''
						: 's'}
				{/if}
			</small>
		</button>

		{#if form.validation?.errors.length}
			<div class="form-error">
				{#each form.validation.errors as validationError}
					<p>{validationError}</p>
				{/each}
			</div>
		{:else if form.error}
			<p class="form-error">{form.error}</p>
		{/if}

		<div class="run-footer">
			{#if !form.canRun && missingSteps.length > 0 && !form.submitting}
				<p class="run-hint">To run, choose {listPhrase(missingSteps)}.</p>
			{/if}
			<button
				class="run-button"
				type="button"
				disabled={!form.canRun || form.submitting}
				onclick={form.runBenchmark}
			>
				{form.submitting ? 'Starting run...' : 'Run benchmark'}
			</button>
		</div>
	</aside>
</section>

<AdvancedRompConfigPanel
	open={advancedPanelOpen}
	{form}
	onClose={closeManualConfig}
	focusSection={panelFocusSection}
/>

<style>
	.setup-workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: clamp(1rem, 3vw, 2rem);
		height: calc(100vh - 9rem);
		min-height: 34rem;
	}

	.setup-workspace.has-plan {
		grid-template-columns: minmax(0, 1fr) minmax(22rem, 30rem);
	}

	/* Two answers side by side need the room the plan panel is using. */
	.setup-workspace.has-plan.is-comparing {
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

	.review-panel {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		min-height: 0;
		padding: clamp(1rem, 2vw, 1.25rem);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		box-shadow: var(--shadow-soft);
	}

	.review-header h2 {
		margin: 0;
		font-family: var(--font-display);
		font-size: clamp(1.5rem, 3vw, 2rem);
		font-weight: 650;
		line-height: 1.05;
		color: var(--color-text);
	}

	.review-header p {
		margin: 0.75rem 0 0;
		color: var(--color-text-muted);
	}

	.eyebrow {
		margin: 0;
		color: var(--color-accent);
		font-size: 0.78rem;
		font-weight: 750;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}

	.state-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 0.5rem;
	}

	.state-pill {
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		color: var(--color-text-muted);
		padding: 0.3rem 0.6rem;
		font-size: 0.75rem;
		font-weight: 750;
	}

	.state-pill.runnable {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
	}

	.spec-list {
		border-top: 1px solid var(--color-border-subtle);
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.spec-list div {
		display: grid;
		grid-template-columns: minmax(7rem, 0.8fr) minmax(0, 1.2fr);
		gap: 1rem;
		padding: 0.8rem 0;
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.spec-list div:last-child {
		border-bottom: 0;
	}

	.spec-list span {
		color: var(--color-text-muted);
	}

	.spec-list strong {
		color: var(--color-text);
		text-align: right;
	}

	.slot-action {
		justify-self: end;
		border: 0;
		background: transparent;
		padding: 0;
		color: var(--color-accent);
		font: inherit;
		font-weight: 800;
		text-align: right;
		cursor: pointer;
	}

	.slot-action:hover {
		text-decoration: underline;
	}

	.advanced-button {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		width: 100%;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
		padding: 0.85rem 1rem;
		font: inherit;
		color: var(--color-text);
		text-align: left;
		cursor: pointer;
	}

	.advanced-button span {
		color: var(--color-text);
		font-weight: 850;
	}

	.advanced-button small {
		color: var(--color-text-muted);
		font-weight: 750;
		white-space: nowrap;
	}

	.advanced-button:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.advanced-button:not(:disabled):hover {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}

	@media (max-width: 30rem) {
		.advanced-button {
			align-items: flex-start;
			flex-direction: column;
		}

		.advanced-button small {
			white-space: normal;
		}
	}

	.form-error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.88rem;
	}

	.form-error p {
		margin: 0.25rem 0;
	}

	.run-footer {
		margin-top: auto;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.run-hint {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.85rem;
		text-align: center;
	}

	.run-button {
		border: 0;
		border-radius: 0.45rem;
		background: var(--color-accent);
		color: white;
		padding: 0.95rem 1rem;
		font: inherit;
		font-weight: 800;
		cursor: pointer;
	}

	.run-button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}

	@media (max-width: 54rem) {
		.setup-workspace,
		.setup-workspace.has-plan {
			grid-template-columns: 1fr;
			height: auto;
		}

		.setup-chat {
			min-height: 36rem;
		}

		.spec-list div {
			grid-template-columns: 1fr;
			gap: 0.25rem;
		}

		.spec-list strong {
			text-align: left;
		}
	}
</style>
