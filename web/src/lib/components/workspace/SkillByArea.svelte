<script lang="ts">
	import { failureMessage } from '$lib/api/core';
	import { getSkillByArea, type AreaLevel, type SkillByArea } from '$lib/api/skill-by-area';
	import { windowLabel } from '$lib/metric-metadata';
	import {
		AREA_CHOICES,
		ZONE_MEASURES,
		measureRows,
		missingModelsNote,
		zoneNote,
		zoneTable,
		zoneWindow,
		type MeasureKey,
		type ZoneModelJob,
		type ZoneSort
	} from '$lib/workspace/skill-by-area';
	import { nextSort, sortState } from '$lib/workspace/table-sort';
	import SortButton from './SortButton.svelte';

	interface Props {
		workspaceId: string;
		runKey: string;
		/** The lead window to open on when the run has it. */
		initialWindow?: string;
		/** Changes when another model of the run finishes, so the table is read again. */
		finishedKey?: string;
		/** The run's jobs, so a model that has no column is accounted for. */
		jobs?: ZoneModelJob[];
	}

	let { workspaceId, runKey, initialWindow, finishedKey = '', jobs = [] }: Props = $props();

	let level = $state<AreaLevel | ''>('');
	let chosenWindow = $state<string | null>(null);
	// Onset error first: where each model is good or bad at the date is the main question.
	let measure = $state<MeasureKey>(ZONE_MEASURES[0].key);
	let sort = $state<ZoneSort>(null);
	let data = $state<SkillByArea | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	const window = $derived(data ? zoneWindow(data, chosenWindow ?? initialWindow) : null);
	const table = $derived(data ? zoneTable(data, window) : null);
	const rows = $derived(table ? measureRows(table, measure, sort) : []);
	const missingModels = $derived(data ? missingModelsNote(jobs, data.models) : null);
	const measureLabel = $derived(ZONE_MEASURES.find((each) => each.key === measure)?.label ?? '');

	$effect(() => {
		// Named so a newly finished model refetches without being used in the request.
		void finishedKey;
		let stale = false;
		loading = true;
		getSkillByArea(workspaceId, runKey, level ? { level } : {})
			.then((loaded) => {
				if (stale) return;
				data = loaded;
				error = null;
			})
			.catch((e) => {
				if (stale) return;
				data = null;
				error = failureMessage(e);
			})
			.finally(() => {
				if (!stale) loading = false;
			});
		return () => {
			stale = true;
		};
	});
</script>

<figure class="by-zone">
	<div class="controls">
		<label>
			<span class="dim">Areas</span>
			<select bind:value={level}>
				{#each AREA_CHOICES as choice (choice.value)}
					<option value={choice.value}>{choice.label}</option>
				{/each}
			</select>
		</label>
		<div class="switch" role="group" aria-label="Measure">
			{#each ZONE_MEASURES as each (each.key)}
				<button
					type="button"
					class:active={each.key === measure}
					aria-pressed={each.key === measure}
					onclick={() => (measure = each.key)}
				>
					{each.label}
				</button>
			{/each}
		</div>
		{#if data && data.windows.length > 1}
			<div class="switch" role="group" aria-label="Lead-time window">
				{#each data.windows as each (each)}
					<button
						type="button"
						class:active={each === window}
						aria-pressed={each === window}
						onclick={() => (chosenWindow = each)}
					>
						{windowLabel(each)}
					</button>
				{/each}
			</div>
		{/if}
	</div>

	{#if loading && !table}
		<p class="dim" role="status">Working out skill by area…</p>
	{:else if error}
		<p class="error" role="alert">Skill by area could not be shown. {error}</p>
	{:else if table && table.rows.length === 0 && table.notCovered.length === 0}
		<p class="dim">This run has no areas to show skill for.</p>
	{:else if table && data}
		{#if missingModels}<p class="missing">{missingModels}</p>{/if}
		<div class="scroll" class:stale={loading} aria-busy={loading}>
			<table>
				<thead>
					<tr>
						<th scope="col">Area</th>
						{#each table.columns as column, i (i)}
							<th scope="col" class="num" aria-sort={sortState(sort, i)}>
								<SortButton
									label={column}
									state={sortState(sort, i)}
									onSort={() => (sort = nextSort(sort, i))}
								/>
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.area + (row.parent ?? ''))}
						<tr>
							<th scope="row">
								{row.area}
								<span class="dim aside">
									{row.parent ? `${row.parent} · ${row.cells}` : row.cells}
								</span>
							</th>
							{#each row.values as cell, i (i)}
								<td class="num" class:best={cell.best}>
									{cell.text}{#if cell.best}<span class="sr-only">, best</span>{/if}
								</td>
							{/each}
						</tr>
					{/each}
					{#each table.notCovered as area (area)}
						<tr class="not-covered">
							<th scope="row">{area}</th>
							<td colspan={table.columns.length}>Not covered by this run</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<figcaption>
			{measureLabel}, {windowLabel(window ?? '').toLowerCase()}. {zoneNote(data)}
			{#if table.notCovered.length > 0}
				An area is not covered when no scored grid cell is centred in it.
			{/if}
		</figcaption>
	{/if}
</figure>

<style>
	.by-zone {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		min-width: 0;
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-4);
		font-size: var(--text-size-label);
	}
	.controls label {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}
	select {
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	.switch {
		display: inline-flex;
		flex-wrap: wrap;
		gap: var(--space-1);
	}
	.switch button {
		padding: 0.15rem 0.55rem;
		border: 1px solid var(--color-border);
		border-radius: 999rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		font: inherit;
		cursor: pointer;
	}
	.switch button.active {
		border-color: var(--color-accent);
		color: var(--color-accent);
		font-weight: 600;
	}
	.scroll {
		max-height: 32rem;
		overflow: auto;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
	}
	.scroll.stale {
		opacity: 0.6;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--text-size-label);
	}
	th,
	td {
		padding: 0.3rem 0.6rem;
		border-bottom: 1px solid var(--color-border-subtle);
		text-align: left;
	}
	thead th {
		position: sticky;
		top: 0;
		background: var(--color-surface-muted);
		font-weight: 600;
		vertical-align: bottom;
	}
	/* The area's name may wrap, so the values keep their columns at the stage's narrowest. */
	tbody th {
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	thead .num {
		white-space: normal;
	}
	.best {
		font-weight: 700;
	}
	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}
	.aside {
		display: block;
	}
	.not-covered {
		color: var(--color-text-muted);
	}
	tbody tr:last-child td,
	tbody tr:last-child th {
		border-bottom: 0;
	}
	figcaption,
	.dim {
		margin: 0;
		font-size: var(--text-size-caption);
		font-weight: 400;
		color: var(--color-text-dim);
	}
	.missing {
		margin: 0;
		font-size: var(--text-size-label);
		color: var(--color-text-muted);
	}
	.error {
		margin: 0;
		font-size: var(--text-size-label);
		color: var(--color-danger);
	}
</style>
