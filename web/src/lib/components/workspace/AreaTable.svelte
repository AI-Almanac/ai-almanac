<script lang="ts">
	import {
		defaultSortKey,
		latestIssueDate,
		parseAreaTable,
		percent,
		rowsOn,
		valuesNote,
		windowLabel,
		AREA_KEY
	} from '$lib/workspace/area-table';
	import { nextSort, sortState, type TableSort } from '$lib/workspace/table-sort';
	import SortButton from './SortButton.svelte';
	import { formatDay } from '$lib/format/dates';
	import CsvTable from './CsvTable.svelte';

	interface Props {
		text: string;
		/** Whether the values are one run's area shares rather than chances. */
		shares?: boolean;
	}

	let { text, shares = false }: Props = $props();

	const table = $derived(parseAreaTable(text));
	let chosenDate = $state<string | null>(null);
	// Unsorted by hand, the table reads two weeks out, highest first.
	let sort = $state<TableSort<string>>(null);
	let allColumns = $state(false);

	const issueDate = $derived(chosenDate ?? latestIssueDate(table));
	const order = $derived<TableSort<string>>(
		sort ?? { key: defaultSortKey(table), direction: 'descending' }
	);
	const rows = $derived(rowsOn(table, issueDate, order));
	const hasParents = $derived(rows.some((row) => row.parent));

	/** Names read A to Z first, chances highest first. */
	function sortBy(key: string) {
		sort = nextSort(order, key, key === AREA_KEY ? 'ascending' : 'descending');
	}
</script>

{#if allColumns}
	<div class="area-table">
		<button type="button" class="link" onclick={() => (allColumns = false)}>By area</button>
		<CsvTable {text} />
	</div>
{:else if table.issueDates.length === 0}
	<p class="dim">This table has no areas.</p>
{:else}
	<figure class="area-table">
		<div class="controls">
			<label>
				<span class="dim">Issued</span>
				<select bind:value={chosenDate}>
					{#each [...table.issueDates].reverse() as date (date)}
						<option value={date} selected={date === issueDate}>{formatDay(date)}</option>
					{/each}
				</select>
			</label>
			<span class="dim">
				{rows.length}
				{rows.length === 1 ? 'area' : 'areas'} · {table.issueDates.length} issue dates
			</span>
			<button type="button" class="link" onclick={() => (allColumns = true)}>All columns</button>
		</div>
		<div class="scroll">
			<table>
				<thead>
					<tr>
						<th scope="col" aria-sort={sortState(order, AREA_KEY)}>
							<SortButton
								label="Area"
								state={sortState(order, AREA_KEY)}
								onSort={() => sortBy(AREA_KEY)}
							/>
						</th>
						{#each table.columns as column (column.key)}
							<th scope="col" class="num" aria-sort={sortState(order, column.key)}>
								<SortButton
									label={column.label}
									state={sortState(order, column.key)}
									onSort={() => sortBy(column.key)}
								/>
							</th>
						{/each}
						<th scope="col">Most likely</th>
						<th scope="col" class="num">Covered</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.area + (row.parent ?? ''))}
						<tr>
							<td>
								{row.area}
								{#if hasParents && row.parent}<span class="dim parent">{row.parent}</span>{/if}
							</td>
							{#each table.columns as column (column.key)}
								{@const value = row.within[column.key]}
								<td class="num shade" style:--p={value ?? 0}>{percent(value)}</td>
							{/each}
							<td>
								{windowLabel(row.mostLikely)}
								{#if row.mostLikelyShare !== null}
									<span class="dim">{percent(row.mostLikelyShare)}</span>
								{/if}
							</td>
							<td class="num dim">{percent(row.coverage)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<figcaption>{valuesNote(shares)}</figcaption>
	</figure>
{/if}

<style>
	.area-table {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		margin: 0;
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem 1rem;
		font-size: 0.8rem;
	}
	.controls label {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}
	select {
		padding: 0.15rem 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	.link {
		margin-left: auto;
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: 0.8rem;
		cursor: pointer;
	}
	.scroll {
		max-height: 32rem;
		overflow: auto;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8rem;
	}
	th,
	td {
		padding: 0.3rem 0.6rem;
		border-bottom: 1px solid var(--color-border-subtle);
		text-align: left;
		white-space: nowrap;
	}
	th {
		position: sticky;
		top: 0;
		background: var(--color-surface-muted);
		font-weight: 600;
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	/* Darker as the value rises, so a column reads as a map without one. */
	.shade {
		background: color-mix(in oklab, var(--color-accent) calc(var(--p) * 60%), transparent);
	}
	.parent {
		margin-left: 0.4rem;
		font-size: var(--text-size-caption);
	}
	tbody tr:last-child td {
		border-bottom: 0;
	}
	figcaption,
	.dim {
		margin: 0;
		font-size: var(--text-size-caption);
		color: var(--color-text-dim);
	}
</style>
