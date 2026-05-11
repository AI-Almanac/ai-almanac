<script lang="ts">
	import type { Dataset, ModelConfig, Region } from '$lib/api';
	import PerModelConfig from './PerModelConfig.svelte';

	type SharedParamValue = string | number | null;
	type ModelOverrideValue = string | boolean | number;

	interface Props {
		open: boolean;
		regions: Region[];
		datasets: Dataset[];
		dataLoaded: boolean;
		models: ModelConfig[];
		selectedRegionId: string;
		selectedDatasetId: string;
		selectedModelIds: string[];
		forecastWindowDays: number | null;
		selectedRegion: Region | null;
		selectedDataset: Dataset | null;
		sharedAdvancedParams: Record<string, SharedParamValue>;
		setRegionId: (id: string) => void;
		setDatasetId: (id: string) => void;
		setForecastWindowDays: (days: number | null) => void;
		toggleModel: (id: string) => void;
		setSharedParam: (key: string, value: SharedParamValue) => void;
		getOverride: <T>(modelId: string, key: string, fallback: T) => T;
		setOverride: (modelId: string, key: string, value: ModelOverrideValue) => void;
		onClose: () => void;
	}

	const {
		open,
		regions,
		datasets,
		dataLoaded,
		models,
		selectedRegionId,
		selectedDatasetId,
		selectedModelIds,
		forecastWindowDays,
		selectedRegion,
		selectedDataset,
		sharedAdvancedParams,
		setRegionId,
		setDatasetId,
		setForecastWindowDays,
		toggleModel,
		setSharedParam,
		getOverride,
		setOverride,
		onClose
	}: Props = $props();

	let activeTab = $state<'plan' | 'models' | 'shared' | 'perModel'>('plan');

	const selectedModels = $derived(models.filter((model) => selectedModelIds.includes(model.id)));

	function isSelected(modelId: string) {
		return selectedModelIds.includes(modelId);
	}

	function closeOnEscape(event: KeyboardEvent) {
		if (open && event.key === 'Escape') onClose();
	}

	function handleDatasetChange(id: string) {
		setDatasetId(id);
		const dataset = datasets.find((item) => item.id === id);
		if (dataset?.obs_file_pattern) setSharedParam('obs_file_pattern', dataset.obs_file_pattern);
	}
</script>

<svelte:window onkeydown={closeOnEscape} />

{#if open}
	<div class="panel-layer" role="presentation">
		<button class="scrim" type="button" aria-label="Close manual configuration" onclick={onClose}
		></button>
		<div class="config-panel" aria-modal="true" role="dialog" aria-labelledby="advanced-title">
			<header class="panel-header">
				<div>
					<p class="eyebrow">Benchmark setup</p>
					<h2 id="advanced-title">Manual configuration</h2>
					<p class="panel-subtitle">
						{selectedRegion?.display_name ?? 'No region'} · {selectedDataset?.name ?? 'No ground truth'}
					</p>
				</div>
				<button class="icon-button" type="button" aria-label="Close manual configuration" onclick={onClose}
					>×</button
				>
			</header>

			<nav class="tabs" aria-label="Manual benchmark setting sections">
				<button
					type="button"
					class:active={activeTab === 'plan'}
					onclick={() => (activeTab = 'plan')}
				>
					Plan
				</button>
				<button
					type="button"
					class:active={activeTab === 'models'}
					onclick={() => (activeTab = 'models')}
				>
					Models
				</button>
				<button
					type="button"
					class:active={activeTab === 'shared'}
					onclick={() => (activeTab = 'shared')}
				>
					Shared settings
				</button>
				<button
					type="button"
					class:active={activeTab === 'perModel'}
					onclick={() => (activeTab = 'perModel')}
				>
					Model run windows
				</button>
			</nav>

			<div class="panel-body">
				{#if activeTab === 'plan'}
					<section class="tab-section" aria-label="Core benchmark settings">
						<div class="section-heading">
							<div>
								<h3>Plan</h3>
								<p>Set the core benchmark inputs before choosing detailed model settings.</p>
							</div>
						</div>

						<div class="settings-columns basic-settings">
							<fieldset>
								<legend>Benchmark inputs</legend>
								<label>
									<span>Region</span>
									<select value={selectedRegionId} onchange={(e) => setRegionId((e.target as HTMLSelectElement).value)}>
										<option value="">Choose a region</option>
										{#each regions.filter((region) => region.has_data) as region}
											<option value={region.id}>{region.display_name}</option>
										{/each}
									</select>
								</label>

								<label>
									<span>Ground truth</span>
									<select
										value={selectedDatasetId}
										disabled={!dataLoaded || datasets.length === 0}
										onchange={(e) => handleDatasetChange((e.target as HTMLSelectElement).value)}
									>
										{#if datasets.length === 0}
											<option value="">{dataLoaded ? 'No datasets available' : 'Loading datasets...'}</option>
										{:else}
											{#each datasets as dataset}
												<option value={dataset.id}>{dataset.name}</option>
											{/each}
										{/if}
									</select>
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
										<option value={15}>Days 1-15</option>
										<option value={30}>Days 1-30</option>
										<option value={45}>Days 1-45</option>
									</select>
								</label>
							</fieldset>
						</div>
					</section>
				{:else if activeTab === 'models'}
					<section class="tab-section" aria-label="Model selection">
						<div class="section-heading">
							<div>
								<h3>Models</h3>
								<p>Choose the forecast systems included in this benchmark.</p>
							</div>
							<strong>{selectedModelIds.length} selected</strong>
						</div>

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
										{#if model.probabilistic}
											<small>Ensemble forecast</small>
										{/if}
									</span>
									<span>{model.model_type}</span>
									<span>{model.start_date.slice(0, 7)} to {model.end_date.slice(0, 7)}</span>
								</label>
							{/each}
						</div>
					</section>
				{:else if activeTab === 'shared'}
					<section class="tab-section" aria-label="Shared benchmark settings">
						<div class="section-heading">
							<div>
								<h3>Shared settings</h3>
								<p>Settings applied to every selected model in this benchmark.</p>
							</div>
						</div>

						<div class="settings-columns">
							<fieldset>
								<legend>Ground truth data</legend>
								<label>
									<span>Data path override</span>
									<input
										value={sharedAdvancedParams.obs ?? ''}
										oninput={(e) => setSharedParam('obs', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Filename pattern</span>
									<input
										value={sharedAdvancedParams.obs_file_pattern ?? ''}
										placeholder={'{}.nc'}
										oninput={(e) =>
											setSharedParam('obs_file_pattern', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Variable name</span>
									<input
										value={sharedAdvancedParams.obs_var ?? ''}
										placeholder="RAINFALL"
										oninput={(e) =>
											setSharedParam('obs_var', (e.target as HTMLInputElement).value)}
									/>
								</label>
							</fieldset>

							<fieldset>
								<legend>Event detection</legend>
								<label>
									<span>Wet-day threshold</span>
									<input
										type="number"
										step="any"
										value={sharedAdvancedParams.wet_threshold ?? ''}
										oninput={(e) =>
											setSharedParam('wet_threshold', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Minimum wet-day rainfall</span>
									<input
										type="number"
										step="any"
										value={sharedAdvancedParams.wet_init ?? ''}
										oninput={(e) =>
											setSharedParam('wet_init', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Wet spell length</span>
									<input
										type="number"
										value={sharedAdvancedParams.wet_spell ?? ''}
										oninput={(e) =>
											setSharedParam('wet_spell', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Dry spell limit</span>
									<input
										type="number"
										value={sharedAdvancedParams.dry_spell ?? ''}
										oninput={(e) =>
											setSharedParam('dry_spell', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Dry spell search extension</span>
									<input
										type="number"
										value={sharedAdvancedParams.dry_extent ?? ''}
										oninput={(e) =>
											setSharedParam('dry_extent', (e.target as HTMLInputElement).value)}
									/>
								</label>
							</fieldset>

							<fieldset>
								<legend>Masks and baseline</legend>
								<label>
									<span>Area mask file</span>
									<input
										value={sharedAdvancedParams.nc_mask ?? ''}
										oninput={(e) =>
											setSharedParam('nc_mask', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Onset threshold file</span>
									<input
										value={sharedAdvancedParams.thresh_file ?? ''}
										oninput={(e) =>
											setSharedParam('thresh_file', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Baseline forecast</span>
									<input
										value={sharedAdvancedParams.ref_model ?? ''}
										placeholder="climatology"
										oninput={(e) =>
											setSharedParam('ref_model', (e.target as HTMLInputElement).value)}
									/>
								</label>
								<label>
									<span>Baseline data path</span>
									<input
										value={sharedAdvancedParams.ref_model_dir ?? ''}
										oninput={(e) =>
											setSharedParam('ref_model_dir', (e.target as HTMLInputElement).value)}
									/>
								</label>
							</fieldset>
						</div>
					</section>
				{:else}
					<section class="tab-section" aria-label="Per-model benchmark settings">
						<div class="section-heading">
							<div>
								<h3>Model run windows</h3>
								<p>Set evaluation dates and model-specific options for each selected model.</p>
							</div>
							<strong>{selectedModels.length} models</strong>
						</div>

						{#if selectedModels.length === 0}
							<p class="empty">Select at least one model before editing model-specific settings.</p>
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
					</section>
				{/if}
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

	.tabs {
		display: flex;
		gap: 0.35rem;
		padding: 0.75rem clamp(1rem, 2vw, 1.5rem);
		border-bottom: 1px solid var(--color-border-subtle);
		overflow-x: auto;
	}

	.tabs button {
		border: 1px solid transparent;
		border-radius: 0.4rem;
		background: transparent;
		color: var(--color-text-muted);
		padding: 0.55rem 0.75rem;
		font: inherit;
		font-weight: 800;
		white-space: nowrap;
		cursor: pointer;
	}

	.tabs button.active {
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
		color: var(--color-accent);
	}

	.panel-body {
		flex: 1;
		min-height: 0;
		overflow: auto;
		padding: clamp(1rem, 2vw, 1.5rem);
	}

	.tab-section {
		display: flex;
		flex-direction: column;
		gap: 1rem;
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

	.basic-settings {
		max-width: 42rem;
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
