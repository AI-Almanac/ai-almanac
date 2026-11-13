<script lang="ts">
	import { resultTable } from '$lib/chat/tools';
	import type { ResultCardProps } from './step-context';

	const { call }: ResultCardProps = $props();

	const table = $derived(resultTable(call.name, call.result));
</script>

{#if table}
	<figure class="card">
		<div class="scroll">
			<table>
				<thead>
					<tr>
						{#each table.columns as column (column)}
							<th scope="col">{column}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each table.rows as row, index (index)}
						<tr>
							{#each row as cell, cellIndex (cellIndex)}
								<td>{cell}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<figcaption>{table.caption}</figcaption>
	</figure>
{/if}

<style>
	.card {
		margin: 0 0 0 1.5rem;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.scroll {
		overflow-x: auto;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.74rem;
		font-variant-numeric: tabular-nums;
	}
	th,
	td {
		padding: 0.3rem 0.55rem;
		text-align: left;
		white-space: nowrap;
		border-bottom: 1px solid var(--color-border-subtle);
	}
	th {
		font-weight: 600;
		background: var(--color-surface-raised);
		color: var(--color-text-muted);
	}
	tbody tr:last-child td {
		border-bottom: 0;
	}
	figcaption {
		font-size: 0.68rem;
		color: var(--color-text-dim);
	}
</style>
