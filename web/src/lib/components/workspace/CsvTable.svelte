<script lang="ts">
	import { numericColumns, parseCsv } from '$lib/workspace/file-preview';

	let { text }: { text: string } = $props();

	const table = $derived(parseCsv(text));
	const numeric = $derived(numericColumns(table));
</script>

{#if table.columns.length === 0}
	<p class="dim">This table is empty.</p>
{:else}
	<figure class="table">
		<div class="scroll">
			<table>
				<thead>
					<tr>
						{#each table.columns as column, index (index)}
							<th scope="col" class:num={numeric[index]}>{column}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each table.rows as row, rowIndex (rowIndex)}
						<tr>
							{#each table.columns as _, index (index)}
								<td class:num={numeric[index]}>{row[index] ?? ''}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<figcaption>
			{table.totalRows > table.rows.length
				? `First ${table.rows.length} of ${table.totalRows.toLocaleString()} rows. Download the file for the rest.`
				: `${table.totalRows.toLocaleString()} ${table.totalRows === 1 ? 'row' : 'rows'}, ${table.columns.length} columns`}
		</figcaption>
	</figure>
{/if}

<style>
	.table {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin: 0;
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
