<script lang="ts">
	import { onMount } from 'svelte';
	import AdminGuard from '$lib/components/AdminGuard.svelte';
	import { getRulesetFeedback, type RulesetFeedback } from '$lib/api';

	let feedback = $state<RulesetFeedback[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(() => {
		getRulesetFeedback()
			.then((rows) => {
				feedback = rows;
			})
			.catch((e) => {
				error = (e as Error).message;
			})
			.finally(() => {
				loading = false;
			});
	});
</script>

<AdminGuard>
	{#if error}<p class="banner error">{error}</p>{/if}

	<section class="card">
		<h2>Collected feedback</h2>
		<p class="hint">
			Every rating in one place: thumbs on ordinary chat turns and votes from comparisons — both the
			admin playground and the blind A/B users run from the benchmark and blend chats — grouped by
			the ruleset version that produced the answer. Deleted rulesets keep their rows here.
		</p>
		<p class="hint">
			Ratings and comparisons only come from users who turn on comparison mode, so read these counts
			as a sample of engaged users rather than of everyone.
		</p>
		{#if loading}
			<p class="empty">Loading…</p>
		{:else if feedback.length === 0}
			<p class="empty">No rated turns yet.</p>
		{:else}
			<div class="scroll">
				<table>
					<thead>
						<tr>
							<th>Ruleset</th>
							<th>Turns</th>
							<th>Rated</th>
							<th>Wins</th>
							<th>Losses</th>
							<th>Ties</th>
							<th>Flags</th>
						</tr>
					</thead>
					<tbody>
						{#each feedback as row (row.ruleset_id + ':' + row.ruleset_version)}
							<tr>
								<td>{row.ruleset_id} v{row.ruleset_version ?? '?'}</td>
								<td>{row.turns}</td>
								<td>{row.rated}</td>
								<td>{row.wins}</td>
								<td>{row.losses}</td>
								<td>{row.ties}</td>
								<td class="flags">
									{#each Object.entries(row.flag_counts) as [flag, count] (flag)}
										<span class="tag">{flag}: {count}</span>
									{:else}
										—
									{/each}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="hint">
				Flags are measure-only signals from the turn log — a decimal quoted without reading data, a
				fired guardrail the answer never mentions, grid-point detail on a small sample. They are
				counted, never enforced.
			</p>
		{/if}
	</section>
</AdminGuard>

<style>
	.banner.error {
		margin: 0;
		padding: 0.6rem 0.75rem;
		border-radius: 0.45rem;
		font-size: 0.82rem;
		color: var(--color-status-failed);
		background: var(--color-status-failed-bg);
		border: 1px solid var(--color-status-failed);
	}
	.scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8rem;
	}
	th,
	td {
		text-align: left;
		padding: 0.35rem 0.75rem 0.35rem 0;
		border-bottom: 1px solid var(--color-border);
		white-space: nowrap;
	}
	th {
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-muted);
	}
	.flags {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
		white-space: normal;
	}
</style>
