<script lang="ts">
	import { getBlendSummary, type JobArtifact } from '$lib/api';
	import { groupBlendOutputs, parseYearlyScores, type YearlyScore } from './blend-outputs';

	let {
		jobId,
		artifacts,
		ondownload
	}: { jobId: string; artifacts: JobArtifact[]; ondownload: (artifact: JobArtifact) => void } =
		$props();

	const groups = $derived(groupBlendOutputs(artifacts));
	const primary = $derived(groups.filter((g) => g.key !== 'other'));
	const other = $derived(groups.find((g) => g.key === 'other'));

	let yearly = $state<YearlyScore[]>([]);

	$effect(() => {
		const id = jobId;
		const hasYearly = artifacts.some(
			(a) => a.filename.startsWith('yearly_metrics_global') && a.filename.endsWith('.csv')
		);
		if (!hasYearly) {
			yearly = [];
			return;
		}
		let cancelled = false;
		void (async () => {
			try {
				const text = await getBlendSummary(id, 'yearly');
				if (!cancelled) yearly = parseYearlyScores(text);
			} catch {
				if (!cancelled) yearly = [];
			}
		})();
		return () => {
			cancelled = true;
		};
	});

	const showModel = $derived(new Set(yearly.map((r) => r.model)).size > 1);

	function fmt(value: number | null): string {
		return value == null ? '—' : value.toFixed(3);
	}
</script>

{#snippet fileList(files: JobArtifact[])}
	<ul>
		{#each files as artifact (artifact.id)}
			<li>
				<button type="button" class="artifact" onclick={() => ondownload(artifact)}>
					<span class="artifact-name">{artifact.filename}</span>
					<span class="size">{(artifact.size_bytes / 1024).toFixed(0)} KB</span>
				</button>
			</li>
		{/each}
	</ul>
{/snippet}

{#if yearly.length > 0}
	<div class="group">
		<h3>Scores by held-out year</h3>
		<p class="hint">
			Absolute scores, not relative to climatology. Each year is scored with weights fitted without
			it. Lower Brier Score and Ranked Probability Score are better; higher Area Under ROC Curve is
			better.
		</p>
		<table>
			<thead>
				<tr>
					<th scope="col">Year</th>
					{#if showModel}<th scope="col">Model</th>{/if}
					<th scope="col">Brier Score</th>
					<th scope="col">Ranked Probability Score</th>
					<th scope="col">Area Under ROC Curve</th>
				</tr>
			</thead>
			<tbody>
				{#each yearly as row (`${row.year}-${row.model}`)}
					<tr>
						<td>{row.year}</td>
						{#if showModel}<td>{row.model}</td>{/if}
						<td>{fmt(row.brier)}</td>
						<td>{fmt(row.rps)}</td>
						<td>{fmt(row.auc)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

{#each primary as group (group.key)}
	<div class="group">
		<h3>{group.label}</h3>
		{@render fileList(group.files)}
	</div>
{/each}

{#if other}
	<details class="group">
		<summary>{other.label} ({other.files.length})</summary>
		<p class="hint">
			Intermediate pipeline files and logs, kept for reproducing or debugging a run.
		</p>
		{@render fileList(other.files)}
	</details>
{/if}

<style>
	.group {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		margin-top: 0.85rem;
	}

	h3,
	summary {
		margin: 0;
		font-size: 0.85rem;
		font-weight: 700;
		color: var(--color-text);
	}

	summary {
		cursor: pointer;
	}

	.hint,
	.size {
		margin: 0;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}

	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.artifact {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 1rem;
		width: 100%;
		padding: 0.6rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-bg);
		cursor: pointer;
		text-align: left;
	}

	.artifact:hover {
		border-color: var(--color-accent-border);
	}

	.artifact-name {
		font-weight: 650;
		color: var(--color-text);
		font-family: var(--font-mono);
		font-size: 0.85rem;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}

	th,
	td {
		padding: 0.35rem 0.6rem;
		border-bottom: 1px solid var(--color-border-subtle);
		text-align: right;
	}

	th:first-child,
	td:first-child {
		text-align: left;
	}

	th {
		font-weight: 650;
		color: var(--color-text-muted);
	}
</style>
