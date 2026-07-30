<script lang="ts">
	import { BASEMAP_STYLES, BOUNDARY_LEVELS, type BasemapStyleId } from './constants';
	import { modelRunLabel, viewModeDescription } from './mapUi';
	import type {
		BoundaryLevel,
		MapViewMode,
		MetricDef,
		MetricWindowAvailability,
		MetricWindowAvailabilityByJob,
		RunDef,
		WindowDef
	} from './types';

	type Props = {
		panelCollapsed: boolean;
		metrics: MetricDef[];
		activeWindows: WindowDef[];
		metricWindowAvailability?: MetricWindowAvailability;
		metricWindowAvailabilityByJob?: MetricWindowAvailabilityByJob;
		activeRuns: RunDef[];
		availableModelRuns: RunDef[];
		selectedMetric: string;
		selectedWindow: string;
		selectedModelJobId: string;
		selectedReferenceJobId: string;
		selectedReferenceWindow: string;
		selectedBasemap: BasemapStyleId;
		viewMode: MapViewMode;
		visibleBoundaryLevels: Set<BoundaryLevel>;
		boundaryLoading: Set<BoundaryLevel>;
		boundaryErrors: Partial<Record<BoundaryLevel, string>>;
		onTogglePanel: () => void;
		onSelectMetric: (value: string) => void;
		onSelectWindow: (value: string) => void;
		onSelectModel: (value: string) => void;
		onSelectViewMode: (value: MapViewMode) => void;
		onSelectReferenceJob: (value: string) => void;
		onSelectReferenceWindow: (value: string) => void;
		onSelectBasemap: (value: BasemapStyleId) => void;
		onToggleBoundary: (value: BoundaryLevel) => void;
	};

	let {
		panelCollapsed,
		metrics,
		activeWindows,
		metricWindowAvailability,
		metricWindowAvailabilityByJob,
		activeRuns,
		availableModelRuns,
		selectedMetric,
		selectedWindow,
		selectedModelJobId,
		selectedReferenceJobId,
		selectedReferenceWindow,
		selectedBasemap,
		viewMode,
		visibleBoundaryLevels,
		boundaryLoading,
		boundaryErrors,
		onTogglePanel,
		onSelectMetric,
		onSelectWindow,
		onSelectModel,
		onSelectViewMode,
		onSelectReferenceJob,
		onSelectReferenceWindow,
		onSelectBasemap,
		onToggleBoundary
	}: Props = $props();

	function availabilityForJob(jobId: string): MetricWindowAvailability | undefined {
		return metricWindowAvailabilityByJob?.[jobId] ?? metricWindowAvailability;
	}

	const selectableMetrics = $derived(
		metrics.filter((metric) => {
			const availability = availabilityForJob(selectedModelJobId);
			return !availability || availability[metric.value]?.includes(selectedWindow);
		})
	);

	const selectableReferenceWindows = $derived(
		activeWindows.filter((window) => {
			const availability =
				selectedReferenceJobId === 'climatology'
					? metricWindowAvailability
					: availabilityForJob(selectedReferenceJobId);
			return !availability || availability[selectedMetric]?.includes(window.value);
		})
	);
</script>

<div class="layer-panel result-lens" class:collapsed={panelCollapsed}>
	<button class="layer-panel-header" onclick={onTogglePanel}>
		<span class="layer-panel-title">Map controls</span>
		<span class="panel-toggle">{panelCollapsed ? '▸' : '▾'}</span>
	</button>

	{#if !panelCollapsed}
		<div class="lens-controls">
			<div class="control-row primary-row">
				<label class="control-field">
					<span>Metric</span>
					<select
						value={selectedMetric}
						onchange={(event) => onSelectMetric(event.currentTarget.value)}
					>
						{#each selectableMetrics as metric}
							<option value={metric.value}>{metric.label}</option>
						{/each}
					</select>
				</label>

				<label class="control-field">
					<span>Lead time</span>
					<select
						value={selectedWindow}
						onchange={(event) => onSelectWindow(event.currentTarget.value)}
					>
						{#each activeWindows as window}
							<option value={window.value}>{window.label}</option>
						{/each}
					</select>
				</label>

				<label class="control-field model-field">
					<span>Model</span>
					<select
						value={selectedModelJobId}
						onchange={(event) => onSelectModel(event.currentTarget.value)}
					>
						{#each availableModelRuns as run}
							<option value={run.jobId}>{modelRunLabel(run)}</option>
						{/each}
					</select>
				</label>
			</div>

			<div class="control-row mode-row">
				<div class="view-toggle" aria-label="Map view">
					<button class:active={viewMode === 'single'} onclick={() => onSelectViewMode('single')}>
						Values
					</button>
					<button
						class:active={viewMode === 'baseline'}
						onclick={() => onSelectViewMode('baseline')}
					>
						Skill
					</button>
					<button
						class:active={viewMode === 'difference'}
						onclick={() => onSelectViewMode('difference')}
					>
						Difference
					</button>
					<button class:active={viewMode === 'swipe'} onclick={() => onSelectViewMode('swipe')}>
						Swipe
					</button>
				</div>
				<p class="lens-note">{viewModeDescription(viewMode)}</p>
			</div>

			<div class="control-row secondary-row">
				{#if viewMode === 'difference' || viewMode === 'swipe'}
					<label class="control-field">
						<span>Compare with</span>
						<select
							value={selectedReferenceJobId}
							onchange={(event) => onSelectReferenceJob(event.currentTarget.value)}
						>
							{#if activeRuns.some((run) => run.modelName === 'climatology')}
								<option value="climatology">Traditional Climatology</option>
							{/if}
							{#each availableModelRuns as run}
								<option value={run.jobId}>{modelRunLabel(run)}</option>
							{/each}
						</select>
					</label>
					<label class="control-field">
						<span>Compare lead time</span>
						<select
							value={selectedReferenceWindow}
							onchange={(event) => onSelectReferenceWindow(event.currentTarget.value)}
						>
							{#each selectableReferenceWindows as window}
								<option value={window.value}>{window.label}</option>
							{/each}
						</select>
					</label>
				{:else if viewMode === 'baseline'}
					<p class="lens-note">Blue is better for error metrics; red is worse.</p>
				{/if}
			</div>
		</div>

		<div class="map-context-row">
			<label class="control-field basemap-field">
				<span>Basemap</span>
				<select
					value={selectedBasemap}
					onchange={(event) => onSelectBasemap(event.currentTarget.value as BasemapStyleId)}
				>
					{#each BASEMAP_STYLES as style}
						<option value={style.id}>{style.label}</option>
					{/each}
				</select>
			</label>

			<details class="boundary-group">
				<summary class="run-header">
					<span class="run-label">Boundaries</span>
				</summary>

				<div class="boundary-options">
					{#each Object.entries(BOUNDARY_LEVELS) as [level, def]}
						{@const boundaryLevel = level as BoundaryLevel}
						{@const isVisible = visibleBoundaryLevels.has(boundaryLevel)}
						{@const isLoading = boundaryLoading.has(boundaryLevel)}
						{@const err = boundaryErrors[boundaryLevel]}

						<div class="layer-item" class:visible={isVisible}>
							<button class="layer-row" onclick={() => onToggleBoundary(boundaryLevel)}>
								<span class="layer-checkbox" class:checked={isVisible}>
									{#if isVisible}<span class="checkmark">✓</span>{/if}
								</span>
								<span class="layer-label">{def.label}</span>
								<span class="osm-level-chip">{def.type}</span>
								{#if isLoading}
									<span class="layer-spinner"></span>
								{:else if err}
									<span class="layer-error" title={err}>✕</span>
								{/if}
							</button>
							{#if err}
								<p class="boundary-error">{err}</p>
							{/if}
						</div>
					{/each}
				</div>
			</details>
		</div>
	{/if}
</div>

<style>
	.layer-panel {
		position: relative;
		order: 2;
		z-index: 30;
		background: rgba(255, 253, 248, 0.98);
		border-top: 1px solid var(--color-border-subtle);
		width: 100%;
		overflow: hidden;
		max-height: 45%;
		overflow-y: auto;
	}

	:global(.map-root.fullscreen) .layer-panel {
		position: absolute;
		left: 50%;
		bottom: 1rem;
		transform: translateX(-50%);
		width: min(58rem, calc(100% - 2rem));
		border: 1px solid rgba(215, 208, 194, 0.9);
		border-radius: 0.5rem;
		box-shadow: 0 0.6rem 2rem rgba(0, 0, 0, 0.28);
		max-height: min(36dvh, 18rem);
		background: rgba(255, 253, 248, 0.94);
		backdrop-filter: blur(8px);
		overflow-y: auto;
	}

	:global(.map-root.fullscreen) .layer-panel.collapsed {
		width: min(18rem, calc(100% - 2rem));
		left: 1rem;
		transform: none;
	}

	:global(.map-root.fullscreen) .layer-panel-header {
		padding: 0.42rem 0.65rem;
	}

	:global(.map-root.fullscreen) .lens-controls {
		gap: 0.45rem;
		padding: 0.55rem 0.65rem;
	}

	:global(.map-root.fullscreen) .primary-row {
		grid-template-columns: minmax(8rem, 1fr) minmax(7rem, 0.85fr) minmax(8rem, 1fr);
	}

	:global(.map-root.fullscreen) .mode-row {
		grid-template-columns: minmax(14rem, 0.9fr) minmax(12rem, 1.1fr);
	}

	:global(.map-root.fullscreen) .control-field select {
		font-size: 0.72rem;
		padding: 0.34rem 0.45rem;
	}

	:global(.map-root.fullscreen) .view-toggle button {
		font-size: 0.64rem;
		padding: 0.28rem 0.25rem;
	}

	:global(.map-root.fullscreen) .lens-note {
		font-size: 0.64rem;
	}

	:global(.map-root.fullscreen) .boundary-group {
		margin-bottom: 0;
	}

	:global(body.figure-lightbox-open .map-root) .layer-panel,
	:global(.map-root.fullscreen.obscured-by-lightbox) .layer-panel {
		display: none;
	}

	.lens-controls {
		display: flex;
		flex-direction: column;
		gap: 0.55rem;
		padding: 0.65rem 0.7rem 0.5rem;
		border-top: 1px solid #e1e5ea;
		background: transparent;
	}

	.control-row {
		display: grid;
		align-items: end;
		gap: 0.65rem;
	}

	.primary-row {
		grid-template-columns: minmax(10rem, 1.15fr) minmax(8rem, 0.9fr) minmax(12rem, 1.4fr);
	}

	.mode-row {
		grid-template-columns: minmax(18rem, 0.9fr) minmax(14rem, 1.1fr);
		align-items: center;
	}

	.secondary-row {
		grid-template-columns: minmax(12rem, 1fr) minmax(10rem, 1fr) minmax(12rem, 1fr);
		align-items: center;
	}

	.map-context-row {
		display: flex;
		align-items: end;
		gap: 0.65rem;
		padding: 0 0.7rem 0.55rem;
	}

	.basemap-field {
		width: min(18rem, 100%);
	}

	.control-field {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		min-width: 0;
	}

	.model-field {
		min-width: 0;
	}

	.control-field span {
		font-size: 0.58rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.09em;
		color: #6f6b62;
	}

	.control-field select {
		width: 100%;
		min-width: 0;
		border: 1px solid #d7d0c2;
		border-radius: 0.35rem;
		background: rgba(255, 255, 255, 0.9);
		color: #2d2a25;
		font: inherit;
		font-size: 0.78rem;
		padding: 0.45rem 0.5rem;
	}

	.view-toggle {
		display: flex;
		align-self: stretch;
		padding: 0.18rem;
		border: 1px solid #d7d0c2;
		border-radius: 0.45rem;
		background: #eee8dd;
	}

	.view-toggle button {
		flex: 1;
		border: none;
		border-radius: 0.32rem;
		background: transparent;
		color: #6f6b62;
		font: inherit;
		font-size: 0.68rem;
		font-weight: 700;
		padding: 0.35rem 0.3rem;
		cursor: pointer;
	}

	.view-toggle button + button {
		margin-left: 0.05rem;
	}

	.view-toggle button.active {
		background: white;
		color: var(--color-accent);
		box-shadow: 0 1px 3px rgba(31, 26, 18, 0.12);
	}

	.lens-note {
		margin: 0;
		font-size: 0.72rem;
		line-height: 1.35;
		color: #6f6b62;
		align-self: center;
	}

	.layer-panel-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		width: 100%;
		padding: 0.42rem 0.7rem 0.34rem;
		border: none;
		background: rgba(246, 242, 234, 0.75);
		cursor: pointer;
		font-family: var(--font-body);
	}

	.layer-panel-header:hover {
		background: rgba(238, 232, 221, 0.82);
	}

	.layer-panel-title {
		font-size: 0.58rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: #888;
		margin: 0;
	}

	.panel-toggle {
		font-size: 0.6rem;
		color: #aaa;
	}

	.boundary-group {
		margin: 0;
		width: min(13.5rem, 100%);
		background: rgba(246, 248, 250, 0.72);
		border: 1px solid #d8dde5;
		border-radius: 0.4rem;
		overflow: hidden;
	}

	.run-header {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.45rem 0.65rem;
		cursor: pointer;
		list-style: none;
	}

	.run-header::-webkit-details-marker {
		display: none;
	}

	.boundary-group .run-header::after {
		content: '▾';
		margin-left: auto;
		color: #8a857a;
		font-size: 0.65rem;
	}

	.boundary-group:not([open]) .run-header::after {
		content: '▸';
	}

	.boundary-options {
		border-top: 1px solid #d8dde5;
		padding: 0.25rem 0;
		background: rgba(255, 255, 255, 0.68);
	}

	.run-label {
		font-size: 0.72rem;
		font-weight: 700;
		color: #222;
	}

	.layer-item {
		display: flex;
		flex-direction: column;
	}

	.layer-row {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		width: 100%;
		padding: 0.28rem 0.5rem;
		border: none;
		background: none;
		cursor: pointer;
		font-family: var(--font-body);
		transition: background-color 0.1s;
		box-sizing: border-box;
	}

	.layer-row:hover:not(:disabled) {
		background: rgba(0, 0, 0, 0.04);
	}

	.layer-checkbox {
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 0.2rem;
		border: 1.5px solid #bbb;
		background: white;
		flex-shrink: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		transition:
			background-color 0.1s,
			border-color 0.1s;
	}

	.layer-checkbox.checked {
		background: var(--color-accent, #3b82f6);
		border-color: var(--color-accent, #3b82f6);
	}

	.checkmark {
		font-size: 0.55rem;
		color: white;
		line-height: 1;
		font-weight: 700;
	}

	.layer-label {
		font-size: 0.75rem;
		font-weight: 500;
		color: #333;
		flex: 1;
		text-align: left;
	}

	.layer-item.visible .layer-label {
		font-weight: 600;
		color: #111;
	}

	.layer-spinner {
		width: 0.65rem;
		height: 0.65rem;
		border: 1.5px solid #ddd;
		border-top-color: #888;
		border-radius: 50%;
		animation: spin 0.7s linear infinite;
		flex-shrink: 0;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.layer-error {
		font-size: 0.7rem;
		color: #c00;
	}

	.osm-level-chip {
		font-size: 0.52rem;
		font-weight: 700;
		line-height: 1;
		color: #596273;
		background: #eef1f4;
		border: 1px solid #d8dde5;
		padding: 0.16rem 0.28rem;
		border-radius: 0.22rem;
		flex-shrink: 0;
	}

	.boundary-error {
		margin: 0;
		padding: 0 0.5rem 0.35rem 1.85rem;
		color: #9b1c1c;
		font-size: 0.62rem;
		line-height: 1.25;
	}
</style>
