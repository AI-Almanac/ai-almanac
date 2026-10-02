<script lang="ts">
	import type { JobSkillScores, MetricDefinition } from '$lib/api';
	import { getCachedJobSkillScores } from '$lib/benchmarks.svelte';
	import { loadMetricDefinitions, metricLabel, metricMap, windowLabel } from '$lib/metric-metadata';
	import { EXTRA_METRIC_LABELS, OVERALL_METRIC_ORDER, formatSkillValue } from '$lib/skill-series';

	type Props = { jobId: string };
	let { jobId }: Props = $props();

	let skill = $state<JobSkillScores | null>(null);
	let fetchError = $state<string | null>(null);
	let metricDefinitions = $state<MetricDefinition[]>([]);
	const definitionsById = $derived(metricMap(metricDefinitions));

	$effect(() => {
		skill = null;
		fetchError = null;
		getCachedJobSkillScores(jobId)
			.then((data) => (skill = data))
			.catch((e) => (fetchError = e instanceof Error ? e.message : 'Failed to load scores'));
	});

	$effect(() => {
		loadMetricDefinitions().then((definitions) => (metricDefinitions = definitions));
	});

	function label(metric: string): string {
		return EXTRA_METRIC_LABELS[metric] ?? metricLabel(metric, definitionsById);
	}
</script>

{#if fetchError}
	<p class="note">Failed to load scores: {fetchError}</p>
{:else if !skill}
	<p class="note">Loading scores…</p>
{:else if skill.windows.length === 0}
	<p class="note">No metric data found.</p>
{:else}
	<p class="note">Probabilistic scores, pooled over the whole region.</p>
	{#each skill.windows as win (win.window)}
		<section class="window-section">
			<h3 class="window-heading">{win.model.toUpperCase()} — {windowLabel(win.window)}</h3>
			<table>
				<tbody>
					{#each OVERALL_METRIC_ORDER.filter((metric) => win.overall[metric] != null) as metric (metric)}
						<tr>
							<td class="metric-name">{label(metric)}</td>
							<td>{formatSkillValue(win.overall[metric])}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>
	{/each}
{/if}

<style>
	.note {
		color: var(--color-text-dim);
		font-size: 0.85rem;
		margin: 0;
	}

	.window-section {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.window-heading {
		font-size: 0.75rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		margin: 0;
	}

	table {
		border-collapse: collapse;
		font-size: 0.8rem;
		width: fit-content;
	}

	td {
		padding: 0.3rem 0.9rem 0.3rem 0;
		border-bottom: 1px solid var(--color-border-subtle);
		font-variant-numeric: tabular-nums;
	}

	.metric-name {
		color: var(--color-text-dim);
	}
</style>
