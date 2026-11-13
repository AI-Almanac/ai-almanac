<script lang="ts">
	import InfoTip from '$lib/components/InfoTip.svelte';
	import type { Dataset, GuardrailThresholds, Region, RompDefaults } from '$lib/api';
	import { isFocusUnits, type BboxExtent, type FocusAreaValue } from '$lib/api/jobs';
	import FocusAreaMap from '$lib/components/FocusAreaMap.svelte';
	import { regionCountry } from '$lib/places';
	import AdvancedRompConfigPanel from './AdvancedRompConfigPanel.svelte';
	import type { BenchmarkSetupForm, BenchmarkStart } from './setup-form.svelte';

	let {
		form,
		regions,
		datasets,
		dataLoaded,
		parameterDefaults,
		guardrails,
		chatAvailable = false,
		startFrom = null,
		settingsOpen = $bindable(false)
	}: {
		/** The setup being filled in; its host may let an assistant fill it in too. */
		form: BenchmarkSetupForm;
		regions: Region[];
		datasets: Dataset[];
		dataLoaded: boolean;
		parameterDefaults: RompDefaults | null;
		guardrails: GuardrailThresholds | null;
		/** An assistant beside the plan can fill it in, so the plan says so. */
		chatAvailable?: boolean;
		/** An earlier benchmark's setup to begin from, applied once the choices have loaded. */
		startFrom?: BenchmarkStart | null;
		/** The full benchmark settings are open over the plan. */
		settingsOpen?: boolean;
	} = $props();

	$effect(() => {
		form.regions = regions;
		form.datasets = datasets;
		form.parameterDefaults = parameterDefaults;
		form.guardrails = guardrails;
		form.dataLoaded = dataLoaded;
	});

	let started = false;
	$effect(() => {
		if (started || !startFrom || !dataLoaded) return;
		started = true;
		void form.startFrom(startFrom);
	});

	type ConfigSection = 'plan' | 'models';
	let panelFocusSection = $state<ConfigSection | null>(null);

	function openConfig(section: ConfigSection | null = null) {
		panelFocusSection = section;
		settingsOpen = true;
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
					: form.spec?.model_names.join(', ') || null,
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
			form.selectedModelIds.length === 0 && 'at least one model'
		].filter((step): step is string => Boolean(step))
	);

	function listPhrase(items: string[]): string {
		if (items.length <= 1) return items[0] ?? '';
		return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
	}

	const focusArea = $derived(
		(form.sharedAdvancedParams.focus_area as FocusAreaValue | null | undefined) ?? null
	);
	const regionExtent = $derived.by((): BboxExtent | null => {
		const r = form.selectedRegion;
		if (!r || r.lat_min == null || r.lat_max == null || r.lon_min == null || r.lon_max == null)
			return null;
		return { lat_min: r.lat_min, lat_max: r.lat_max, lon_min: r.lon_min, lon_max: r.lon_max };
	});

	function closeManualConfig() {
		settingsOpen = false;
		panelFocusSection = null;
		void form.syncBenchmarkConfig({ showErrors: true });
	}
</script>

<aside class="review-panel" data-tour="benchmark-plan">
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
				Describe the benchmark you want in the chat and the assistant will fill this plan in — or
				configure it yourself.
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

	<div class="focus-area" data-tour="focus-area">
		<div class="focus-area-head">
			<span class="label-with-help">
				Area of interest
				<InfoTip
					text="Limits scoring to part of the data: draw a box, or pick a country or the states and districts inside one on the map. Leave it as the whole region to score everything."
				/>
			</span>
			<small
				>{focusArea
					? isFocusUnits(focusArea)
						? `Scoring limited to ${focusArea.units.length} ${focusArea.units.length === 1 ? 'area' : 'areas'}`
						: 'Scoring limited to the box'
					: 'Optional: score part of the region'}</small
			>
		</div>
		{#if form.selectedDatasetId}
			<FocusAreaMap
				value={focusArea}
				extent={regionExtent}
				country={regionCountry(regions, form.selectedRegionId || null)}
				onchange={(box) => form.setSharedParam('focus_area', box)}
			/>
		{:else}
			<p class="focus-area-hint">
				Choose observations, then pick a place or draw a box here to score only part of them.
			</p>
		{/if}
	</div>

	<button
		class="advanced-button"
		type="button"
		data-tour="manual-config"
		onclick={() => openConfig()}
	>
		<span>Configure it yourself</span>
		<small>
			{#if form.syncingConfig}
				Validating...
			{:else}
				{form.selectedModelIds.length} selected model{form.selectedModelIds.length === 1 ? '' : 's'}
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

<AdvancedRompConfigPanel
	open={settingsOpen}
	{form}
	onClose={closeManualConfig}
	focusSection={panelFocusSection}
/>

<style>
	.review-panel {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		min-height: 0;
		/* The area map can outgrow the viewport; scroll the plan rather than spill it. */
		overflow-y: auto;
		padding: clamp(1rem, 2vw, 1.25rem);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface);
		flex: 1 1 auto;
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
		.spec-list div {
			grid-template-columns: 1fr;
			gap: 0.25rem;
		}

		.spec-list strong {
			text-align: left;
		}
	}

	.focus-area {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.focus-area-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.75rem;
	}

	.focus-area-head small {
		opacity: 0.7;
	}

	.focus-area-hint {
		margin: 0;
		opacity: 0.7;
		font-size: 0.9em;
	}

	.label-with-help {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}
</style>
