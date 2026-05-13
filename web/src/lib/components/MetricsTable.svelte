<script lang="ts">
	import { type JobMetrics, type WindowMetrics, type BboxFilter, type GridInfo } from '$lib/api';
	import { getCachedJobMetrics } from '$lib/benchmarks.svelte';

	type Props = { jobId: string };
	let { jobId }: Props = $props();

	// Fetch state
	let metricsData = $state<JobMetrics | null>(null);
	let loading = $state(false);
	let fetchError = $state<string | null>(null);
	let grid = $state<GridInfo | null>(null); // full domain grid, set once

	// Bbox filter state
	let bboxInput = $state({ lat_min: '', lat_max: '', lon_min: '', lon_max: '' });
	let appliedBbox = $state<BboxFilter | undefined>(undefined);
	let bboxError = $state<string | null>(null);
	const bboxActive = $derived(appliedBbox !== undefined);

	// Extent and spacing derived from grid for input constraints
	const extent = $derived(
		grid
			? {
					lat_min: grid.lats[0],
					lat_max: grid.lats[grid.lats.length - 1],
					lon_min: grid.lons[0],
					lon_max: grid.lons[grid.lons.length - 1],
					lat_step: grid.lats.length > 1 ? grid.lats[1] - grid.lats[0] : 1,
					lon_step: grid.lons.length > 1 ? grid.lons[1] - grid.lons[0] : 1
				}
			: null
	);

	async function fetchMetrics(bbox?: BboxFilter) {
		loading = true;
		fetchError = null;
		try {
			const data = await getCachedJobMetrics(jobId, bbox);
			metricsData = data;
			if (data.grid && !grid) {
				grid = data.grid;
				// Pre-fill inputs with full extent so spinning from a known value feels natural
				const lats = data.grid.lats;
				const lons = data.grid.lons;
				bboxInput = {
					lat_min: String(lats[0]),
					lat_max: String(lats[lats.length - 1]),
					lon_min: String(lons[0]),
					lon_max: String(lons[lons.length - 1])
				};
			}
		} catch (e) {
			fetchError = e instanceof Error ? e.message : 'Failed to load metrics';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		fetchMetrics();
	});

	/** Returns an error string if no grid points fall within the entered bbox, null if valid. */
	function validateBbox(f: BboxFilter): string | null {
		if (!grid) return null;
		const latOk = grid.lats.some(
			(lat) => (f.lat_min == null || lat >= f.lat_min) && (f.lat_max == null || lat <= f.lat_max)
		);
		const lonOk = grid.lons.some(
			(lon) => (f.lon_min == null || lon >= f.lon_min) && (f.lon_max == null || lon <= f.lon_max)
		);
		if (!latOk) {
			const step = grid.lats.length > 1 ? grid.lats[1] - grid.lats[0] : null;
			return `No grid points in that latitude range. Available latitudes: ${grid.lats.join('°, ')}°${step ? ` (${step}° spacing)` : ''}.`;
		}
		if (!lonOk) {
			const step = grid.lons.length > 1 ? grid.lons[1] - grid.lons[0] : null;
			return `No grid points in that longitude range. Available longitudes: ${grid.lons.join('°, ')}°${step ? ` (${step}° spacing)` : ''}.`;
		}
		if (f.lat_min != null && f.lat_max != null && f.lat_min > f.lat_max)
			return 'Latitude minimum must be less than latitude maximum.';
		if (f.lon_min != null && f.lon_max != null && f.lon_min > f.lon_max)
			return 'Longitude minimum must be less than longitude maximum.';
		return null;
	}

	function applyBbox() {
		const f: BboxFilter = {};
		if (bboxInput.lat_min !== '') f.lat_min = parseFloat(bboxInput.lat_min);
		if (bboxInput.lat_max !== '') f.lat_max = parseFloat(bboxInput.lat_max);
		if (bboxInput.lon_min !== '') f.lon_min = parseFloat(bboxInput.lon_min);
		if (bboxInput.lon_max !== '') f.lon_max = parseFloat(bboxInput.lon_max);
		if (!Object.keys(f).length) {
			resetBbox();
			return;
		}
		const err = validateBbox(f);
		if (err) {
			bboxError = err;
			return;
		}
		bboxError = null;
		appliedBbox = f;
		fetchMetrics(appliedBbox);
	}

	function resetBbox() {
		if (grid) {
			bboxInput = {
				lat_min: String(grid.lats[0]),
				lat_max: String(grid.lats[grid.lats.length - 1]),
				lon_min: String(grid.lons[0]),
				lon_max: String(grid.lons[grid.lons.length - 1])
			};
		} else {
			bboxInput = { lat_min: '', lat_max: '', lon_min: '', lon_max: '' };
		}
		bboxError = null;
		appliedBbox = undefined;
		fetchMetrics(undefined);
	}

	// --- Table rendering helpers ---

	const VAR_META: Record<string, { label: string }> = {
		false_alarm_rate: { label: 'False alarm rate' },
		miss_rate: { label: 'Miss rate' },
		mean_mae: { label: 'Mean absolute error' }
	};

	const PRIMARY_VARS = ['false_alarm_rate', 'miss_rate', 'mean_mae'];
	let showPerYear = $state(false);

	function varLabel(key: string): string {
		if (VAR_META[key]) return VAR_META[key].label;
		const m = key.match(/^mae_(\d{4})$/);
		if (m) return `Mean absolute error ${m[1]}`;
		return key;
	}

	function fmt(v: number, unit: string): string {
		if (unit === 'fraction') return (v * 100).toFixed(1) + '%';
		return v.toFixed(1) + ' d';
	}

	function cellColor(v: number, min: number, max: number): string {
		if (max === min) return 'transparent';
		const t = (v - min) / (max - min);
		const r = Math.round(t < 0.5 ? t * 2 * 190 : 190 + (t - 0.5) * 2 * 65);
		const g = Math.round(t < 0.5 ? 160 : 160 * (1 - (t - 0.5) * 2));
		return `rgba(${r},${g},60,0.18)`;
	}

	function visibleVars(win: WindowMetrics): string[] {
		const primary = PRIMARY_VARS.filter((v) => v in win.metrics);
		const perYear = Object.keys(win.metrics)
			.filter((k) => /^mae_\d{4}$/.test(k))
			.sort();
		return showPerYear ? [...primary, ...perYear] : primary;
	}
</script>

<div class="metrics-root">
	<!-- Subregion filter -->
	<div class="bbox-panel" class:active={bboxActive}>
		<div class="bbox-header">
			<span class="bbox-label">Subregion Filter</span>
			{#if bboxActive}
				<span class="bbox-active-badge">Active</span>
			{/if}
			{#if extent}
				<span class="bbox-hint">
					Grid: latitude {extent.lat_min}°–{extent.lat_max}°N, longitude {extent.lon_min}°–{extent.lon_max}°E
					({grid?.lats.length}×{grid?.lons.length} points)
				</span>
			{/if}
		</div>
		<div class="bbox-fields">
			<label
				>Latitude min
				<input
					type="number"
					step={extent?.lat_step ?? 'any'}
					bind:value={bboxInput.lat_min}
					min={extent?.lat_min}
					max={extent?.lat_max}
				/>
			</label>
			<label
				>Latitude max
				<input
					type="number"
					step={extent?.lat_step ?? 'any'}
					bind:value={bboxInput.lat_max}
					min={extent?.lat_min}
					max={extent?.lat_max}
				/>
			</label>
			<label
				>Longitude min
				<input
					type="number"
					step={extent?.lon_step ?? 'any'}
					bind:value={bboxInput.lon_min}
					min={extent?.lon_min}
					max={extent?.lon_max}
				/>
			</label>
			<label
				>Longitude max
				<input
					type="number"
					step={extent?.lon_step ?? 'any'}
					bind:value={bboxInput.lon_max}
					min={extent?.lon_min}
					max={extent?.lon_max}
				/>
			</label>
			<div class="bbox-actions">
				<button class="btn-apply" onclick={applyBbox}>Apply</button>
				{#if bboxActive}
					<button class="btn-reset" onclick={resetBbox}>Reset</button>
				{/if}
			</div>
		</div>
		{#if bboxError}
			<p class="bbox-error">{bboxError}</p>
		{/if}
	</div>

	<!-- Per-year toggle -->
	<div class="controls">
		<label class="toggle">
			<input type="checkbox" bind:checked={showPerYear} />
			Show per-year mean absolute error
		</label>
	</div>

	<!-- Metric glossary -->
	<details class="metric-glossary">
		<summary>About these metrics</summary>
		<div class="glossary-grid">
			<div class="glossary-item">
				<span class="g-term">Mean absolute error</span>
				<span class="g-def"
					><strong>Mean Absolute Error</strong> — average absolute difference in days between the
					model's predicted monsoon onset and the ground-truth observed onset, averaged across all
					model initializations for a year. <em>Lower is better.</em></span
				>
			</div>
			<div class="glossary-item">
				<span class="g-term">False alarm rate</span>
				<span class="g-def"
					><strong>False Alarm Rate</strong> — fraction of forecast windows where the model predicts
					onset but no onset actually occurs (FP ÷ (FP + TN)). A model that over-predicts onset will
					score high here. <em>Lower is better.</em></span
				>
			</div>
			<div class="glossary-item">
				<span class="g-term">Miss rate</span>
				<span class="g-def"
					><strong>Miss Rate</strong> — fraction of forecast windows where onset occurs but the
					model fails to predict it (FN ÷ actual onsets). A model that under-predicts onset will
					score high here. <em>Lower is better.</em></span
				>
			</div>
			<div class="glossary-item">
				<span class="g-term">Days 1–15 / 16–30</span>
				<span class="g-def"
					><strong>Forecast window</strong> — lead-time range in days. Each window groups all model initializations
					whose forecast horizon falls within that range, summarizing skill at near-term vs. extended-range
					prediction.</span
				>
			</div>
			<div class="glossary-item">
				<span class="g-term">Statistics</span>
				<span class="g-def"
					>Columns show the distribution of the metric <em>across grid points</em>: Mean, Min,
					Median (P50), P75, P90, and Max. Heat coloring indicates relative performance — greener
					cells are lower values within each row's range.</span
				>
			</div>
		</div>
	</details>

	<!-- Metrics tables -->
	{#if loading}
		<p class="loading">Loading metrics…</p>
	{:else if fetchError}
		<p class="error">Failed to load metrics: {fetchError}</p>
	{:else if !metricsData || metricsData.windows.length === 0}
		<p class="empty">No metric data found{bboxActive ? ' for this subregion' : ''}.</p>
	{:else}
		{#each metricsData.windows as win}
			{@const vars = visibleVars(win)}
			<section class="window-section">
				<h3 class="window-heading">
					{win.model.toUpperCase()} — Days {win.window}
					{#if win.tolerance_days != null}
						<span class="tolerance">±{win.tolerance_days} day tolerance</span>
					{/if}
				</h3>
				<div class="table-wrap">
					<table>
						<thead>
							<tr>
								<th class="metric-col">Metric</th>
								<th>Mean</th>
								<th>Min</th>
								<th>Median</th>
								<th>P75</th>
								<th>P90</th>
								<th>Max</th>
								<th class="unit-col">Unit</th>
							</tr>
						</thead>
						<tbody>
							{#each vars as varKey}
								{@const s = win.metrics[varKey]}
								{#if s}
									<tr>
										<td class="metric-name">{varLabel(varKey)}</td>
										<td style="background:{cellColor(s.mean, s.min, s.max)}"
											>{fmt(s.mean, s.unit)}</td
										>
										<td style="background:{cellColor(s.min, s.min, s.max)}">{fmt(s.min, s.unit)}</td
										>
										<td style="background:{cellColor(s.p50, s.min, s.max)}">{fmt(s.p50, s.unit)}</td
										>
										<td style="background:{cellColor(s.p75, s.min, s.max)}">{fmt(s.p75, s.unit)}</td
										>
										<td style="background:{cellColor(s.p90, s.min, s.max)}">{fmt(s.p90, s.unit)}</td
										>
										<td style="background:{cellColor(s.max, s.min, s.max)}">{fmt(s.max, s.unit)}</td
										>
										<td class="unit">{s.unit}</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/each}
	{/if}
</div>

<style>
	.metrics-root {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.bbox-panel {
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.5rem;
		padding: 0.85rem 1rem;
		background: var(--color-surface);
		transition: border-color 0.15s;
	}

	.bbox-panel.active {
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}

	.bbox-header {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		margin-bottom: 0.65rem;
		flex-wrap: wrap;
	}

	.bbox-label {
		font-size: 0.65rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--color-text-dim);
	}

	.bbox-active-badge {
		font-size: 0.6rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.1rem 0.4rem;
		border-radius: 0.2rem;
		background: var(--color-accent);
		color: var(--color-bg);
	}

	.bbox-hint {
		font-size: 0.68rem;
		color: var(--color-text-dim);
		margin-left: auto;
		font-family: var(--font-mono);
	}

	.bbox-fields {
		display: flex;
		align-items: flex-end;
		gap: 0.6rem;
		flex-wrap: wrap;
	}

	.bbox-fields label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.72rem;
		font-weight: 500;
		color: var(--color-text-muted);
	}

	.bbox-fields input {
		width: 80px;
		padding: 0.35rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		font-family: var(--font-mono);
		font-size: 0.8rem;
		color: var(--color-text);
		background: var(--color-bg);
		outline: none;
		transition: border-color 0.12s;
	}

	.bbox-fields input:focus {
		border-color: var(--color-accent);
	}

	.bbox-actions {
		display: flex;
		gap: 0.4rem;
		padding-bottom: 0.05rem;
	}

	.btn-apply,
	.btn-reset {
		padding: 0.35rem 0.85rem;
		border-radius: 0.3rem;
		border: none;
		font-family: var(--font-body);
		font-size: 0.78rem;
		font-weight: 600;
		cursor: pointer;
		transition: background-color 0.12s;
	}

	.btn-apply {
		background: var(--color-accent);
		color: var(--color-bg);
	}
	.btn-apply:hover {
		background: var(--color-accent-hover);
	}

	.btn-reset {
		background: var(--color-surface-raised);
		color: var(--color-text-muted);
		border: 1px solid var(--color-border-subtle);
	}
	.btn-reset:hover {
		background: var(--color-border-subtle);
	}

	.bbox-error {
		margin: 0.5rem 0 0 0;
		font-size: 0.75rem;
		color: var(--color-danger);
	}

	.toggle {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		font-size: 0.78rem;
		color: var(--color-text-muted);
		cursor: pointer;
	}
	.toggle input {
		accent-color: var(--color-accent);
		cursor: pointer;
	}

	.loading,
	.empty {
		color: var(--color-text-dim);
		font-size: 0.85rem;
		margin: 0;
	}
	.error {
		color: var(--color-danger);
		font-size: 0.85rem;
		margin: 0;
	}

	.window-section {
		margin-bottom: 1.5rem;
	}

	.window-heading {
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--color-text-dim);
		margin: 0 0 0.6rem 0;
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}

	.tolerance {
		font-size: 0.62rem;
		font-weight: 400;
		text-transform: none;
		letter-spacing: 0;
		color: var(--color-text-dim);
		opacity: 0.7;
	}

	.table-wrap {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8rem;
		color: var(--color-text);
	}

	th {
		padding: 0.4rem 0.75rem;
		text-align: right;
		font-size: 0.65rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-dim);
		border-bottom: 1px solid var(--color-border-subtle);
		white-space: nowrap;
	}

	th.metric-col {
		text-align: left;
	}
	th.unit-col {
		opacity: 0.6;
	}

	td {
		padding: 0.45rem 0.75rem;
		text-align: right;
		font-family: var(--font-mono);
		font-size: 0.78rem;
		border-bottom: 1px solid var(--color-border-subtle);
		white-space: nowrap;
	}

	td.metric-name {
		text-align: left;
		font-family: var(--font-body);
		font-weight: 500;
		color: var(--color-text-muted);
	}

	td.unit {
		font-family: var(--font-body);
		font-size: 0.7rem;
		color: var(--color-text-dim);
		opacity: 0.7;
	}

	tbody tr:last-child td {
		border-bottom: none;
	}
	tbody tr:hover td {
		background-color: var(--color-accent-glow) !important;
	}

	/* ---- Metric glossary ---- */
	.metric-glossary {
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
		overflow: hidden;
	}
	.metric-glossary > summary {
		padding: 0.45rem 0.75rem;
		font-size: 0.7rem;
		font-weight: 600;
		color: var(--color-text-dim);
		cursor: pointer;
		list-style: none;
		user-select: none;
		background: var(--color-surface);
		transition: background-color 0.12s;
	}
	.metric-glossary > summary::-webkit-details-marker {
		display: none;
	}
	.metric-glossary[open] > summary {
		border-bottom: 1px solid var(--color-border-subtle);
	}
	.metric-glossary > summary:hover {
		background: var(--color-accent-glow);
	}

	.glossary-grid {
		display: flex;
		flex-direction: column;
		gap: 0;
	}
	.glossary-item {
		display: flex;
		gap: 0.85rem;
		align-items: baseline;
		padding: 0.5rem 0.75rem;
		border-bottom: 1px solid var(--color-border-subtle);
	}
	.glossary-item:last-child {
		border-bottom: none;
	}

	.g-term {
		font-family: var(--font-mono);
		font-size: 0.72rem;
		font-weight: 700;
		color: var(--color-accent);
		min-width: 90px;
		flex-shrink: 0;
	}
	.g-def {
		font-size: 0.74rem;
		color: var(--color-text-muted);
		line-height: 1.45;
	}
	.g-def strong {
		color: var(--color-text);
		font-weight: 600;
	}
	.g-def em {
		font-style: normal;
		color: var(--color-text-dim);
	}
</style>
