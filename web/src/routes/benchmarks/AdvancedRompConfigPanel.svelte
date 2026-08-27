<script lang="ts">
	import PerModelConfig from './PerModelConfig.svelte';
	import type { BenchmarkSetupForm } from './setup-form.svelte';
	import { getForecastModels, forecastModelFor, type ForecastModel } from '$lib/api';

	interface Props {
		open: boolean;
		form: BenchmarkSetupForm;
		onClose: () => void;
		focusSection?: 'plan' | 'models' | null;
	}

	const { open, form, onClose, focusSection = null }: Props = $props();

	let panelBody = $state<HTMLElement | null>(null);
	$effect(() => {
		if (!open || !focusSection) return;
		panelBody
			?.querySelector(`[data-section="${focusSection}"]`)
			?.scrollIntoView({ block: 'start' });
	});

	const regions = $derived(form.regions);
	const datasets = $derived(form.datasets);
	const dataLoaded = $derived(form.dataLoaded);
	const models = $derived(form.models);
	const selectedRegionId = $derived(form.selectedRegionId);
	const selectedDatasetId = $derived(form.selectedDatasetId);
	const selectedModelIds = $derived(form.selectedModelIds);
	const forecastWindowDays = $derived(form.forecastWindowDays);
	const selectedRegion = $derived(form.selectedRegion);
	const selectedDataset = $derived(form.selectedDataset);
	const selectedModels = $derived(form.selectedModels);
	const sharedAdvancedParams = $derived(form.sharedAdvancedParams);
	const parameterDefaults = $derived(form.parameterDefaults);

	const setRegionId = (id: string) => form.setRegionId(id);
	const setForecastWindowDays = (days: number | null) => form.setForecastWindowDays(days);
	const toggleModel = (id: string) => form.toggleModel(id);
	const setSharedParam = (key: string, value: string | number | null) =>
		form.setSharedParam(key, value);
	const getOverride = <T,>(modelId: string, key: string, fallback: T): T =>
		form.getOverride(modelId, key, fallback);
	const setOverride = (modelId: string, key: string, value: string | boolean | number) =>
		form.setOverride(modelId, key, value);

	function isSelected(modelId: string) {
		return selectedModelIds.includes(modelId);
	}

	// Forecast registry; a model can run live forecasts when its name resolves
	// to an entry (same gate as the blend form). Rejects (feature off) leave
	// the list empty so no badges show.
	let forecastModels = $state<ForecastModel[]>([]);
	$effect(() => {
		getForecastModels()
			.then((list) => (forecastModels = list))
			.catch(() => {});
	});

	function closeOnEscape(event: KeyboardEvent) {
		if (open && event.key === 'Escape') onClose();
	}

	function handleDatasetChange(id: string) {
		form.setDatasetId(id);
		const dataset = datasets.find((item) => item.id === id);
		if (dataset?.obs_file_pattern) setSharedParam('obs_file_pattern', dataset.obs_file_pattern);
	}

	function regionName(regionId: string | null | undefined): string | null {
		if (!regionId) return null;
		return regions.find((region) => region.id === regionId)?.display_name ?? regionId;
	}

	function defaultValue(value: string | number | null | undefined, fallback = 'None') {
		if (value === null || value === undefined || value === '') return fallback;
		return String(value);
	}
</script>

<svelte:window onkeydown={closeOnEscape} />

{#if open}
	<div class="panel-layer" role="presentation">
		<button class="scrim" type="button" aria-label="Close benchmark settings" onclick={onClose}
		></button>
		<div class="config-panel" aria-modal="true" role="dialog" aria-labelledby="advanced-title">
			<header class="panel-header">
				<div>
					<p class="eyebrow">Benchmark setup</p>
					<h2 id="advanced-title">Benchmark settings</h2>
					<p class="panel-subtitle">
						{selectedRegion?.display_name ?? 'No region'} · {selectedDataset?.name ??
							'No ground truth'}
					</p>
				</div>
				<button
					class="icon-button"
					type="button"
					aria-label="Close benchmark settings"
					onclick={onClose}>×</button
				>
			</header>

			<div class="panel-body" bind:this={panelBody}>
				<div class="settings-form">
					<section
						class="form-section plan-section"
						aria-label="Core benchmark settings"
						data-section="plan"
					>
						<div class="section-kicker">1</div>
						<div class="section-content">
							<div class="section-heading">
								<div>
									<h3>
										What to score against <span class="section-badge required">Required</span>
									</h3>
									<p>
										Models are scored on how well they predicted the onset dates observed in this
										record, over this coverage, at these lead times.
									</p>
								</div>
							</div>

							<div class="settings-columns basic-settings">
								<fieldset>
									<legend>Benchmark inputs</legend>
									<label>
										<span>Ground truth</span>
										<select
											value={selectedDatasetId}
											disabled={!dataLoaded || datasets.length === 0}
											onchange={(e) => handleDatasetChange((e.target as HTMLSelectElement).value)}
										>
											{#if datasets.length === 0}
												<option value=""
													>{dataLoaded
														? 'No observation datasets available'
														: 'Loading datasets...'}</option
												>
											{:else}
												<option value="">Choose an observation dataset</option>
												{#each datasets as dataset}
													<option value={dataset.id}>
														{dataset.name}{#if regionName(dataset.region)}
															· {regionName(dataset.region)}{/if}
													</option>
												{/each}
											{/if}
										</select>
										<small>
											Observation data may cover a broader area; ROMP clips it to the benchmark
											coverage.
										</small>
									</label>

									<label>
										<span>Benchmark coverage</span>
										<select
											value={selectedRegionId}
											disabled={!selectedDatasetId}
											onchange={(e) => setRegionId((e.target as HTMLSelectElement).value)}
										>
											<option value="">
												{selectedDatasetId
													? 'Choose benchmark coverage'
													: 'Select ground truth first'}
											</option>
											{#each regions as region}
												<option value={region.id}>{region.display_name}</option>
											{/each}
										</select>
										<small>
											Defaults from the selected dataset. Change it when using broader observations
											for a custom region.
										</small>
									</label>

									<label>
										<span>Forecast window</span>
										<select
											value={forecastWindowDays ?? ''}
											onchange={(e) => {
												const value = (e.target as HTMLSelectElement).value;
												setForecastWindowDays(value ? Number(value) : null);
											}}
										>
											<option value={30}>Days 1-30</option>
											<option value={45}>Days 1-45</option>
										</select>
									</label>
								</fieldset>
							</div>
						</div>
					</section>

					<section class="form-section" aria-label="Model selection" data-section="models">
						<div class="section-kicker">2</div>
						<div class="section-content">
							<div class="section-heading">
								<div>
									<h3>Which models to test <span class="section-badge required">Required</span></h3>
									<p>Only models with forecasts covering the selected region appear here.</p>
								</div>
								<strong>{selectedModelIds.length} selected</strong>
							</div>

							{#if !selectedRegionId}
								<p class="empty">
									Select ground truth and benchmark coverage to see available models.
								</p>
							{:else if models.length === 0}
								<p class="empty">No models are available for the selected region.</p>
							{:else}
								<div class="model-table">
									<div class="model-row header" aria-hidden="true">
										<span>Use</span>
										<span>Model</span>
										<span>Type</span>
										<span>Coverage</span>
									</div>
									{#each models as model}
										<label class="model-row" class:selected={isSelected(model.id)}>
											<span>
												<input
													type="checkbox"
													checked={isSelected(model.id)}
													onchange={() => toggleModel(model.id)}
												/>
											</span>
											<span>
												<strong>{model.display_name}</strong>
												{#if forecastModelFor(forecastModels, model.display_name)}
													<span
														class="forecast-badge"
														title="This model can also generate live forecasts.">Live forecast</span
													>
												{/if}
												{#if model.probabilistic}
													<small>Ensemble forecast</small>
												{/if}
											</span>
											<span>{model.model_type}</span>
											<span>{model.start_date.slice(0, 7)} to {model.end_date.slice(0, 7)}</span>
										</label>
									{/each}
								</div>
								<p class="forecast-legend">
									<span class="forecast-badge">Live forecast</span> models can be extended into the current
									season. Any model can be blended and benchmarked — those without the badge are historical-only.
								</p>
							{/if}
						</div>
					</section>

					<section class="form-section" aria-label="Per-model benchmark settings">
						<div class="section-kicker">3</div>
						<div class="section-content">
							<details class="section-details">
								<summary>
									<div class="section-heading">
										<div>
											<h3>Which years count <span class="section-badge">Defaults applied</span></h3>
											<p>
												More evaluation years give more trustworthy scores — but years that overlap
												a model's training look better than they should, and pre-satellite years
												(before 1979) look worse. Defaults use each model's full available range.
											</p>
										</div>
										<strong>
											{selectedModels.length} model{selectedModels.length === 1 ? '' : 's'}
											<span class="chevron" aria-hidden="true"></span>
										</strong>
									</div>
								</summary>

								<div class="details-body">
									{#if selectedModels.length === 0}
										<p class="empty">
											Select at least one model before editing model-specific settings.
										</p>
									{:else}
										<div class="model-config-list">
											{#each selectedModelIds as modelId}
												<PerModelConfig
													{modelId}
													cfg={models.find((model) => model.id === modelId)}
													{getOverride}
													{setOverride}
												/>
											{/each}
										</div>
									{/if}
								</div>
							</details>
						</div>
					</section>

					<section class="form-section" aria-label="Shared benchmark settings">
						<div class="section-kicker">4</div>
						<div class="section-content">
							<details class="section-details">
								<summary>
									<div class="section-heading">
										<div>
											<h3>
												How onset is detected <span class="section-badge">Defaults applied</span>
											</h3>
											<p>
												These define what counts as a monsoon onset and the baseline skill is
												measured against. The defaults follow the standard ROMP definition — most
												benchmarks should keep them.
											</p>
										</div>
										<strong>Optional <span class="chevron" aria-hidden="true"></span></strong>
									</div>
								</summary>

								<div class="details-body settings-columns">
									<fieldset>
										<legend>Event detection</legend>
										<label>
											<span class="label-with-help">
												Wet-day threshold
												<span
													class="tip"
													title="Rainfall amount used to classify a day as wet when detecting onset behavior."
													>ⓘ</span
												>
											</span>
											<input
												type="number"
												step="any"
												placeholder={defaultValue(parameterDefaults?.wet_threshold)}
												value={sharedAdvancedParams.wet_threshold ?? ''}
												oninput={(e) =>
													setSharedParam('wet_threshold', (e.target as HTMLInputElement).value)}
											/>
											<small>Default: {defaultValue(parameterDefaults?.wet_threshold)} mm</small>
										</label>
										<label>
											<span class="label-with-help">
												Minimum wet-day rainfall
												<span
													class="tip"
													title="Minimum daily rainfall counted toward a wet spell during onset detection."
													>ⓘ</span
												>
											</span>
											<input
												type="number"
												step="any"
												placeholder={defaultValue(parameterDefaults?.wet_init)}
												value={sharedAdvancedParams.wet_init ?? ''}
												oninput={(e) =>
													setSharedParam('wet_init', (e.target as HTMLInputElement).value)}
											/>
											<small>Default: {defaultValue(parameterDefaults?.wet_init)} mm</small>
										</label>
										<label>
											<span class="label-with-help">
												Wet spell length
												<span
													class="tip"
													title="Number of consecutive wet days required before a wet spell can support onset."
													>ⓘ</span
												>
											</span>
											<input
												type="number"
												placeholder={defaultValue(parameterDefaults?.wet_spell)}
												value={sharedAdvancedParams.wet_spell ?? ''}
												oninput={(e) =>
													setSharedParam('wet_spell', (e.target as HTMLInputElement).value)}
											/>
											<small>Default: {defaultValue(parameterDefaults?.wet_spell)} days</small>
										</label>
										<label>
											<span class="label-with-help">
												Dry spell limit
												<span
													class="tip"
													title="Consecutive dry days used to reject false onsets after an initial wet period."
													>ⓘ</span
												>
											</span>
											<input
												type="number"
												placeholder={defaultValue(parameterDefaults?.dry_spell)}
												value={sharedAdvancedParams.dry_spell ?? ''}
												oninput={(e) =>
													setSharedParam('dry_spell', (e.target as HTMLInputElement).value)}
											/>
											<small>Default: {defaultValue(parameterDefaults?.dry_spell)} days</small>
										</label>
										<label>
											<span class="label-with-help">
												Dry spell search extension
												<span
													class="tip"
													title="Extra days searched for dry spells after candidate onset; larger values make onset validation stricter."
													>ⓘ</span
												>
											</span>
											<input
												type="number"
												placeholder={defaultValue(parameterDefaults?.dry_extent)}
												value={sharedAdvancedParams.dry_extent ?? ''}
												oninput={(e) =>
													setSharedParam('dry_extent', (e.target as HTMLInputElement).value)}
											/>
											<small>Default: {defaultValue(parameterDefaults?.dry_extent)} days</small>
										</label>
									</fieldset>

									<fieldset>
										<legend>Masks and baseline</legend>
										<label>
											<span class="label-with-help">
												Area mask file
												<span
													class="tip"
													title="Optional NetCDF mask limiting which grid cells contribute to benchmark metrics."
													>ⓘ</span
												>
											</span>
											<input
												placeholder={defaultValue(parameterDefaults?.nc_mask, 'No mask')}
												value={sharedAdvancedParams.nc_mask ?? ''}
												oninput={(e) =>
													setSharedParam('nc_mask', (e.target as HTMLInputElement).value)}
											/>
											<small>Default: {defaultValue(parameterDefaults?.nc_mask, 'No mask')}</small>
										</label>
										<label>
											<span class="label-with-help">
												Onset threshold file
												<span
													class="tip"
													title="Optional threshold file for onset detection; leave empty to use the standard dataset-derived thresholds."
													>ⓘ</span
												>
											</span>
											<input
												placeholder={defaultValue(
													parameterDefaults?.thresh_file,
													'Dataset default'
												)}
												value={sharedAdvancedParams.thresh_file ?? ''}
												oninput={(e) =>
													setSharedParam('thresh_file', (e.target as HTMLInputElement).value)}
											/>
											<small
												>Default: {defaultValue(
													parameterDefaults?.thresh_file,
													'Dataset default'
												)}</small
											>
										</label>
										<label>
											<span class="label-with-help">
												Baseline forecast
												<span
													class="tip"
													title="Reference forecast used for skill comparisons; climatology is the standard baseline."
													>ⓘ</span
												>
											</span>
											<input
												value={sharedAdvancedParams.ref_model ?? ''}
												placeholder={defaultValue(parameterDefaults?.ref_model, 'climatology')}
												oninput={(e) =>
													setSharedParam('ref_model', (e.target as HTMLInputElement).value)}
											/>
											<small
												>Default: {defaultValue(parameterDefaults?.ref_model, 'climatology')}</small
											>
										</label>
										<label>
											<span class="label-with-help">
												Baseline data path
												<span
													class="tip"
													title="Optional path for baseline forecast files; leave empty to use the benchmark ground-truth data path."
													>ⓘ</span
												>
											</span>
											<input
												placeholder={defaultValue(
													parameterDefaults?.ref_model_dir,
													'Ground truth path'
												)}
												value={sharedAdvancedParams.ref_model_dir ?? ''}
												oninput={(e) =>
													setSharedParam('ref_model_dir', (e.target as HTMLInputElement).value)}
											/>
											<small
												>Default: {defaultValue(
													parameterDefaults?.ref_model_dir,
													'Ground truth path'
												)}</small
											>
										</label>
									</fieldset>
								</div>
							</details>
						</div>
					</section>
				</div>
			</div>

			<footer class="panel-footer">
				<button class="secondary-button" type="button" onclick={onClose}>Done</button>
			</footer>
		</div>
	</div>
{/if}

<style>
	.panel-layer {
		position: fixed;
		inset: 0;
		z-index: 60;
		display: flex;
		justify-content: flex-end;
	}

	.scrim {
		position: absolute;
		inset: 0;
		border: 0;
		background: rgba(30, 27, 23, 0.34);
		cursor: pointer;
	}

	.config-panel {
		position: relative;
		display: flex;
		flex-direction: column;
		width: min(86vw, 74rem);
		height: 100%;
		background: var(--color-surface-raised);
		border-left: 1px solid var(--color-border);
		box-shadow: -1rem 0 3rem rgba(30, 27, 23, 0.18);
	}

	.panel-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		padding: clamp(1rem, 2vw, 1.5rem);
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.eyebrow {
		margin: 0 0 0.35rem;
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 800;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}

	h2,
	h3,
	p {
		margin: 0;
	}

	h2 {
		font-family: var(--font-display);
		font-size: clamp(1.5rem, 3vw, 2.2rem);
		line-height: 1.05;
		color: var(--color-text);
	}

	.panel-subtitle,
	.section-heading p,
	.empty {
		margin-top: 0.35rem;
		color: var(--color-text-muted);
	}

	.forecast-badge {
		font-size: 0.62rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.05rem 0.35rem;
		border-radius: 0.2rem;
		background: rgba(52, 211, 153, 0.15);
		color: var(--color-status-complete);
		white-space: nowrap;
	}

	.forecast-legend {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.4rem;
		margin-top: 0.5rem;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}

	.icon-button {
		width: 2.25rem;
		height: 2.25rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-bg);
		color: var(--color-text);
		font-size: 1.5rem;
		line-height: 1;
		cursor: pointer;
	}

	.panel-body {
		flex: 1;
		min-height: 0;
		overflow: auto;
		padding: clamp(1rem, 2vw, 1.5rem);
	}

	.settings-form {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
		max-width: 72rem;
	}

	.form-section {
		display: grid;
		grid-template-columns: 2.25rem minmax(0, 1fr);
		gap: 0.9rem;
		align-items: start;
	}

	.section-kicker {
		display: grid;
		place-items: center;
		width: 2rem;
		height: 2rem;
		border: 1px solid var(--color-accent-border);
		border-radius: 999px;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font-size: 0.75rem;
		font-weight: 900;
	}

	.section-content {
		display: flex;
		flex-direction: column;
		gap: 0.9rem;
		min-width: 0;
	}

	.section-heading {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}

	.section-heading h3 {
		font-size: 1rem;
		font-weight: 850;
		color: var(--color-text);
	}

	.section-heading strong {
		color: var(--color-text-muted);
		white-space: nowrap;
	}

	.section-badge {
		margin-left: 0.4rem;
		vertical-align: middle;
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		background: var(--color-bg);
		color: var(--color-text-muted);
		padding: 0.12rem 0.5rem;
		font-size: 0.66rem;
		font-weight: 750;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		white-space: nowrap;
	}

	.section-badge.required {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
	}

	.section-details summary {
		cursor: pointer;
		list-style: none;
	}

	.section-details summary::-webkit-details-marker {
		display: none;
	}

	.section-details summary:hover h3 {
		color: var(--color-accent);
	}

	.chevron::after {
		content: '▸';
	}

	.section-details[open] .chevron::after {
		content: '▾';
	}

	.details-body {
		margin-top: 0.9rem;
	}

	.model-table {
		display: flex;
		flex-direction: column;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.5rem;
		overflow: hidden;
	}

	.model-row {
		display: grid;
		grid-template-columns: minmax(2.5rem, 0.2fr) minmax(12rem, 1.5fr) minmax(7rem, 0.6fr) minmax(
				11rem,
				0.8fr
			);
		gap: 0.75rem;
		align-items: center;
		padding: 0.7rem 0.85rem;
		border-bottom: 1px solid var(--color-border-subtle);
		color: var(--color-text-muted);
	}

	.model-row:last-child {
		border-bottom: 0;
	}

	.model-row.header {
		background: var(--color-surface);
		font-size: 0.72rem;
		font-weight: 850;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	label.model-row {
		cursor: pointer;
	}

	.model-row.selected {
		background: var(--color-accent-light);
	}

	.model-row strong {
		display: block;
		color: var(--color-text);
	}

	.model-row small {
		display: block;
		margin-top: 0.1rem;
		color: var(--color-accent);
		font-weight: 750;
	}

	.settings-columns {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr));
		gap: 1rem;
		align-items: start;
	}

	.plan-section .settings-columns {
		grid-template-columns: minmax(min(100%, 22rem), 42rem);
	}

	fieldset {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		margin: 0;
		padding: 1rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.5rem;
	}

	legend {
		padding: 0 0.3rem;
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 850;
		letter-spacing: 0.04em;
		text-transform: uppercase;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		color: var(--color-text-muted);
		font-size: 0.82rem;
		font-weight: 750;
	}

	label small {
		color: var(--color-text-dim);
		font-size: 0.72rem;
		font-weight: 650;
	}

	.label-with-help {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}

	.tip {
		display: inline-grid;
		place-items: center;
		width: 1rem;
		height: 1rem;
		border-radius: 999px;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font-size: 0.7rem;
		font-weight: 900;
		cursor: help;
	}

	input:not([type]),
	input[type='number'],
	select {
		width: 100%;
		box-sizing: border-box;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-bg);
		color: var(--color-text);
		padding: 0.55rem 0.6rem;
		font: inherit;
	}

	input:focus {
		outline: none;
		border-color: var(--color-accent);
		box-shadow: 0 0 0 3px var(--color-accent-light);
	}

	select:focus {
		outline: none;
		border-color: var(--color-accent);
		box-shadow: 0 0 0 3px var(--color-accent-light);
	}

	.model-config-list {
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.5rem;
		overflow: visible;
	}

	.panel-footer {
		display: flex;
		justify-content: flex-end;
		padding: 0.9rem clamp(1rem, 2vw, 1.5rem);
		border-top: 1px solid var(--color-border-subtle);
		background: var(--color-surface);
	}

	.secondary-button {
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-bg);
		color: var(--color-text);
		padding: 0.65rem 1rem;
		font: inherit;
		font-weight: 850;
		cursor: pointer;
	}

	@media (max-width: 44rem) {
		.config-panel {
			width: 100%;
		}

		.form-section {
			grid-template-columns: 1fr;
		}

		.section-kicker {
			display: none;
		}

		.model-row,
		.model-row.header {
			grid-template-columns: minmax(2.5rem, 0.2fr) minmax(0, 1fr);
		}

		.model-row > span:nth-child(3),
		.model-row > span:nth-child(4) {
			display: none;
		}
	}
</style>
