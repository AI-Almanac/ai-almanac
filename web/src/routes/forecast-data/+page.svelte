<script lang="ts">
	import { onMount } from 'svelte';
	import AdminGuard from '$lib/components/AdminGuard.svelte';
	import { getTrajectorySets, type TrajectorySet } from '$lib/api';

	let rows = $state<TrajectorySet[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			rows = await getTrajectorySets();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function coverageCount(set: TrajectorySet): number {
		return set.covered_init_dates?.length ?? 0;
	}

	function timestamp(value: string | null | undefined): string {
		if (!value) return '—';
		const d = new Date(value);
		return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
	}
</script>

<AdminGuard>
	<main class="wrap">
		<header class="head">
			<div>
				<p class="eyebrow">Admin</p>
				<h1>Forecast data</h1>
				<p class="muted">
					Season trajectory sets — the shared, model-scoped rollouts that blend forecasts score
					against. Each set is generated once and reused by every blend and region that uses the
					model.
				</p>
			</div>
			<button type="button" class="ghost" onclick={load} disabled={loading}>
				{loading ? 'Refreshing…' : 'Refresh'}
			</button>
		</header>

		<p class="hint muted">
			To generate a cold set, run a forecast for a blend that uses the model as an administrator —
			the rollout populates this store and every later run scores against it for free.
		</p>

		{#if error}
			<p class="error">{error}</p>
		{:else if loading}
			<p class="muted">Loading…</p>
		{:else if rows.length === 0}
			<p class="muted">No trajectory data has been generated yet.</p>
		{:else}
			<div class="table-scroll">
				<table>
					<thead>
						<tr>
							<th>Model</th>
							<th>Init source</th>
							<th>Season</th>
							<th>Status</th>
							<th>Issue dates cached</th>
							<th>Updated</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as set (set.id)}
							<tr>
								<td>{set.model_name}</td>
								<td>{set.init_source ?? '—'}</td>
								<td>{set.season ?? '—'}</td>
								<td><span class="status status-{set.status}">{set.status}</span></td>
								<td>{coverageCount(set)}</td>
								<td>{timestamp(set.completed_at ?? set.started_at ?? set.created_at)}</td>
							</tr>
							{#if set.error}
								<tr class="error-row">
									<td colspan="6"><span class="error">{set.error}</span></td>
								</tr>
							{/if}
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</main>
</AdminGuard>

<style>
	.wrap {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: clamp(1.5rem, 5vw, 4rem);
		max-width: 64rem;
		margin: 0 auto;
	}

	.head {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
		align-items: flex-start;
		justify-content: space-between;
	}

	.head h1 {
		margin: 0.25rem 0 0.5rem;
		font-family: var(--font-display);
	}

	.eyebrow {
		margin: 0;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		font-size: 0.75rem;
		color: var(--color-text-muted);
	}

	.muted {
		color: var(--color-text-muted);
		margin: 0;
	}

	.hint {
		font-size: 0.9rem;
	}

	.error {
		color: var(--color-danger, #c0392b);
	}

	.table-scroll {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
	}

	th,
	td {
		text-align: left;
		padding: 0.5rem 0.75rem;
		border-bottom: 1px solid var(--color-border, #e2e2e2);
	}

	th {
		font-size: 0.8rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-text-muted);
	}

	.status {
		font-weight: 700;
		font-size: 0.85rem;
	}

	.status-complete {
		color: var(--color-success, #2e7d32);
	}

	.status-failed {
		color: var(--color-danger, #c0392b);
	}

	.error-row td {
		border-bottom: 1px solid var(--color-border, #e2e2e2);
	}
</style>
