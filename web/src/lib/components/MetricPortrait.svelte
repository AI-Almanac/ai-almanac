<script lang="ts">
	/**
	 * The metrics × models portrait — and the metric navigation.
	 *
	 * Rows are metrics (spelled out in full, never abbreviated). Columns are
	 * grouped **window-major**: both lead-time windows are shown at once, and
	 * within each window the competing models sit side by side with climatology
	 * pinned beside them. Model-major grouping would put the same model's two
	 * windows together, which compares a model against itself rather than against
	 * its competitors.
	 *
	 * Cells are shaded by position within their own (row, window) group only —
	 * see metric-portrait.ts for why pooling windows would make the shading
	 * encode lead time instead of model skill.
	 *
	 * Clicking a row promotes that metric to the stage above, which is why this
	 * doubles as the metric picker.
	 */
	import type { Portrait, PortraitRow } from '$lib/metric-portrait';
	import { windowLabel } from '$lib/metric-metadata';
	import { formatSkillValue } from '$lib/skill-series';

	type Props = {
		portrait: Portrait;
		selectedMetric: string | null;
		onSelectMetric: (metric: string) => void;
	};

	let { portrait, selectedMetric, onSelectMetric }: Props = $props();

	let hoveredColumn = $state<string | null>(null);

	const GROUPS = [
		{ key: 'spatial' as const, label: 'Spatial — averaged over the region' },
		{ key: 'probabilistic' as const, label: 'Probabilistic — pooled over the region' }
	];

	const disagreeing = $derived(portrait.rows.filter((row) => row.disagrees));

	function rowsFor(group: 'spatial' | 'probabilistic'): PortraitRow[] {
		return portrait.rows.filter((row) => row.group === group);
	}

	/**
	 * One diverging scale, encoding exactly one thing: skill relative to
	 * climatology. Teal above the reference, muted rust below, unshaded at or
	 * without one.
	 *
	 * Deliberately not rank-within-row. Rank is relative and says nothing when a
	 * single model ran, and using colour for both rank and the climatology
	 * threshold made the two indistinguishable — most of the table came out one
	 * flat pink that couldn't express how far below the reference a value sat.
	 */
	const GOOD = '47, 111, 99'; // --color-accent
	const BAD = '166, 84, 60'; // muted rust; earthier than --color-danger on paper

	function cellStyle(cell: { value: number | null; skill: number | null }): string {
		if (cell.value == null || cell.skill == null) return '';
		// Clamp at ±1: beyond "twice as bad as climatology" the exact magnitude
		// stops being actionable, and letting it run makes everything saturate.
		const magnitude = Math.min(Math.abs(cell.skill), 1);
		if (magnitude < 0.02) return '';
		const alpha = 0.08 + magnitude * 0.42;
		return `background: rgba(${cell.skill < 0 ? BAD : GOOD}, ${alpha.toFixed(3)})`;
	}

	function formatValue(row: PortraitRow, value: number | null): string {
		if (value == null) return '—';
		// Days read naturally with one decimal; scores and fractions want three.
		return row.unit === 'days' ? value.toFixed(1) : formatSkillValue(value);
	}

	function dimmed(key: string): boolean {
		return hoveredColumn !== null && hoveredColumn !== key;
	}
</script>

<section class="portrait" aria-label="All metrics by model and lead time">
	<div class="head">
		<h3>All metrics</h3>
		{#if disagreeing.length > 0}
			<span class="flag">
				◆ {disagreeing.map((row) => row.label).join(', ')}
				{disagreeing.length === 1 ? 'ranks' : 'rank'} the models differently from the rest
			</span>
		{/if}
	</div>

	{#if portrait.rows.length === 0}
		<p class="empty">No metrics available for this run set.</p>
	{:else}
		<div class="scroll">
			<table>
				<thead>
					<tr>
						<th scope="col" rowspan="2" class="mh">Metric</th>
						{#each portrait.windows as window (window)}
							<th scope="colgroup" colspan={portrait.columns.length + 1} class="wh">
								{windowLabel(window)}
							</th>
						{/each}
					</tr>
					<tr>
						{#each portrait.windows as window (window)}
							{#each portrait.columns as column (column.key)}
								<th
									scope="col"
									class="ch"
									class:dim={dimmed(column.key)}
									onmouseenter={() => (hoveredColumn = column.key)}
									onmouseleave={() => (hoveredColumn = null)}
								>
									{column.label}
								</th>
							{/each}
							<th scope="col" class="ch refh">Climatology</th>
						{/each}
					</tr>
				</thead>
				{#each GROUPS as group (group.key)}
					{@const rows = rowsFor(group.key)}
					{#if rows.length > 0}
						<tbody>
							<tr class="grouprow">
								<td colspan={1 + portrait.windows.length * (portrait.columns.length + 1)}>
									{group.label}
								</td>
							</tr>
							{#each rows as row (row.metric)}
								<tr
									class="datarow"
									class:selected={row.metric === selectedMetric}
									onclick={() => onSelectMetric(row.metric)}
								>
									<th scope="row" class="mn">
										<!-- A real button, so the metric picker is reachable by keyboard.
										     The row's own click handler is a convenience for pointers. -->
										<button
											type="button"
											class="mlabel"
											aria-pressed={row.metric === selectedMetric}
											onclick={() => onSelectMetric(row.metric)}
										>
											{row.label}
										</button>
										{#if row.unit && row.unit !== 'dimensionless'}
											<span class="unit">{row.unit}</span>
										{/if}
									</th>
									{#each portrait.windows as window (window)}
										{#each row.cellsByWindow[window] ?? [] as cell (cell.key)}
											<td
												class="c"
												class:dim={dimmed(cell.key)}
												class:best={cell.isBest}
												style={cellStyle(cell)}
												title={cell.worseThanReference ? 'Worse than climatology' : undefined}
											>
												{formatValue(row, cell.value)}
											</td>
										{/each}
										<td class="c ref">
											{formatValue(row, row.referenceByWindow[window] ?? null)}
											{#if row.disagreeingWindows.includes(window)}
												<span
													class="rowflag"
													title="In this window, {row.label} ranks the models differently from the other metrics"
													>◆</span
												>
											{/if}
										</td>
									{/each}
								</tr>
							{/each}
						</tbody>
					{/if}
				{/each}
			</table>
		</div>

		<div class="foot">
			<div class="scale">
				<span class="ramp" aria-hidden="true">
					<span class="sw b3"></span><span class="sw b2"></span><span class="sw b1"></span>
					<span class="sw zero"></span>
					<span class="sw g1"></span><span class="sw g2"></span><span class="sw g3"></span>
				</span>
				<span class="stxt">
					worse than climatology ← · → better than climatology. Shade depth is distance from
					climatology; cells with no reference are left unshaded.
				</span>
			</div>
			<p class="na">
				Not computed by this benchmark: {portrait.notComputed.join(', ')}. An absent metric is not a
				passing score — calibration and ensemble spread are unmeasured here.
			</p>
		</div>
	{/if}
</section>

<style>
	.portrait {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.75rem;
		flex-wrap: wrap;
	}

	h3 {
		margin: 0;
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--color-text-dim);
	}

	.flag {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--color-danger);
		max-width: 34rem;
		text-align: right;
		line-height: 1.4;
	}

	.empty {
		margin: 0;
		padding: 1rem 0;
		font-size: 0.85rem;
		color: var(--color-text-dim);
	}

	.scroll {
		overflow-x: auto;
	}

	table {
		border-collapse: collapse;
		width: 100%;
	}

	th {
		font-size: 0.65rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-dim);
		padding: 0.3rem 0.5rem 0.35rem;
		text-align: center;
	}

	th.wh {
		border-bottom: 1px solid var(--color-border);
		border-left: 1px solid var(--color-border);
		color: var(--color-text);
		font-size: 0.68rem;
	}

	th.ch {
		border-bottom: 1px solid var(--color-border);
		font-size: 0.62rem;
		white-space: nowrap;
	}

	th.mh {
		text-align: left;
		border-bottom: 1px solid var(--color-border);
	}

	th.refh {
		font-style: italic;
	}

	th.dim {
		opacity: 0.4;
	}

	.grouprow td {
		font-size: 0.62rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--color-text-dim);
		padding: 0.6rem 0.15rem 0.25rem;
	}

	tr.datarow {
		cursor: pointer;
	}

	.mlabel {
		padding: 0;
		border: none;
		background: none;
		font: inherit;
		color: inherit;
		text-align: left;
		cursor: pointer;
	}

	.mlabel:focus-visible {
		outline: 2px solid var(--color-accent);
		outline-offset: 2px;
		border-radius: 0.15rem;
	}

	tr.datarow:hover th.mn .mlabel {
		color: var(--color-accent);
	}

	tr.selected th.mn {
		box-shadow: inset 2px 0 0 var(--color-accent);
	}

	tr.selected th.mn .mlabel {
		color: var(--color-accent);
		font-weight: 700;
	}

	th.mn {
		text-align: left;
		text-transform: none;
		letter-spacing: 0;
		font-size: 0.78rem;
		font-weight: 500;
		font-family: var(--font-body);
		color: var(--color-text);
		border-bottom: 1px solid var(--color-border-subtle);
		white-space: nowrap;
		padding-left: 0.5rem;
	}

	.unit {
		color: var(--color-text-dim);
		font-size: 0.68rem;
		margin-left: 0.3rem;
	}

	.rowflag {
		color: var(--color-danger);
		font-size: 0.6rem;
		margin-left: 0.2rem;
		vertical-align: 0.1em;
	}

	td.c {
		font-family: var(--font-mono);
		font-size: 0.76rem;
		text-align: center;
		padding: 0.3rem 0.5rem;
		border: 1px solid var(--color-surface);
		border-bottom: 1px solid var(--color-border-subtle);
		color: var(--color-text);
		white-space: nowrap;
	}

	td.c.best {
		font-weight: 700;
	}

	td.c.ref {
		color: var(--color-text-muted);
		font-style: italic;
		background: var(--color-surface-muted);
		border-right: 1px solid var(--color-border);
	}

	td.c.dim {
		opacity: 0.3;
	}

	.foot {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		margin-top: 0.2rem;
	}

	.scale {
		display: flex;
		align-items: center;
		gap: 0.15rem;
	}

	.ramp {
		display: flex;
		gap: 1px;
		flex: none;
	}

	.sw {
		width: 0.85rem;
		height: 0.55rem;
		border-radius: 0.12rem;
		flex: none;
	}

	/* Mirrors cellStyle: 0.08 + magnitude * 0.42, sampled at 1.0 / 0.5 / 0.15. */
	.b3 {
		background: rgba(166, 84, 60, 0.5);
	}

	.b2 {
		background: rgba(166, 84, 60, 0.29);
	}

	.b1 {
		background: rgba(166, 84, 60, 0.14);
	}

	.zero {
		background: var(--color-surface);
		box-shadow: inset 0 0 0 1px var(--color-border);
	}

	.g1 {
		background: rgba(47, 111, 99, 0.14);
	}

	.g2 {
		background: rgba(47, 111, 99, 0.29);
	}

	.g3 {
		background: rgba(47, 111, 99, 0.5);
	}

	.stxt {
		margin-left: 0.4rem;
		font-size: 0.68rem;
		color: var(--color-text-dim);
		line-height: 1.4;
	}

	.na {
		margin: 0;
		font-size: 0.72rem;
		line-height: 1.5;
		color: var(--color-text-dim);
		max-width: 46rem;
	}
</style>
