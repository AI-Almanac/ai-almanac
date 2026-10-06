<script lang="ts">
	import { lookupModel } from '$lib/data/model-catalog';
	import type { ModelConfig } from '$lib/api';

	interface Props {
		models: ModelConfig[];
		selectedIds: string[];
		dataLoaded: boolean;
		onToggle: (id: string) => void;
	}

	const { models, selectedIds, dataLoaded, onToggle }: Props = $props();

	const selectedWithGaps = $derived(
		models.filter((m) => selectedIds.includes(m.id) && m.missing_years?.length)
	);
</script>

{#if models.length === 0}
	<p class="loading-hint">{dataLoaded ? 'No models available.' : 'Loading models…'}</p>
{:else}
	<div class="model-chip-grid">
		{#each models as m}
			{@const info = lookupModel(m.id)}
			<button
				type="button"
				class="model-chip"
				class:active={selectedIds.includes(m.id)}
				onclick={() => onToggle(m.id)}
				title={info?.desc}
			>
				<span class="chip-name">{m.display_name}</span>
				<span class="chip-meta">
					{info?.type ?? m.model_type} · {info?.resolution ?? ''}
					{m.probabilistic ? ` · ${m.members ?? '?'} mbr` : ''}
				</span>
				<span class="chip-years">{m.start_date.slice(0, 4)}–{m.end_date.slice(0, 4)}</span>
			</button>
		{/each}
	</div>

	{#each selectedWithGaps as m (m.id)}
		<p class="missing-years-note">
			{m.display_name} has no data for {m.missing_years?.join(', ')}; the benchmark skips those
			years.
		</p>
	{/each}

	<details class="model-reference">
		<summary>Model reference</summary>
		<div class="model-ref-body">
			{#each models as m}
				{@const info = lookupModel(m.id)}
				<div class="model-ref-row" class:ref-selected={selectedIds.includes(m.id)}>
					<div class="ref-header">
						<span class="ref-name">{m.display_name}</span>
						<span class="ref-badge ref-badge-{(info?.type ?? m.model_type).toLowerCase()}"
							>{info?.type ?? m.model_type}</span
						>
						<span class="ref-forecast"
							>{info?.forecast ??
								(m.probabilistic
									? `Probabilistic · ${m.members ?? '?'} members`
									: 'Deterministic')}</span
						>
						<span class="ref-res">{info?.resolution ?? '—'}</span>
						<span class="ref-period"
							>{info?.period ?? `${m.start_date.slice(0, 4)}–${m.end_date.slice(0, 4)}`}</span
						>
					</div>
					{#if info?.desc}
						<p class="ref-desc">{info.desc}</p>
					{/if}
				</div>
			{/each}
		</div>
	</details>
{/if}

<style>
	.loading-hint {
		font-size: 0.78rem;
		color: var(--color-text-dim);
		margin: 0.25rem 0 0;
	}

	.model-chip-grid {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		padding-top: 0.25rem;
	}

	.model-chip {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.1rem;
		padding: 0.45rem 0.9rem;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		font-family: var(--font-body);
		cursor: pointer;
		transition:
			background-color 0.12s,
			color 0.12s,
			border-color 0.12s,
			box-shadow 0.12s;
		text-align: left;
	}
	.model-chip:hover {
		border-color: var(--color-accent);
		color: var(--color-accent);
		box-shadow: 0 0 0 3px var(--color-accent-glow);
	}
	.model-chip.active {
		background: var(--color-accent-light);
		border-color: var(--color-accent);
		color: var(--color-accent);
	}
	.chip-name {
		font-size: 0.78rem;
		font-weight: 600;
	}
	.chip-meta {
		font-size: 0.6rem;
		font-family: var(--font-mono);
		opacity: 0.65;
	}
	.chip-years {
		font-size: 0.62rem;
		font-family: var(--font-mono);
		opacity: 0.75;
	}
	.model-chip.active .chip-years {
		opacity: 0.9;
	}
	.model-chip.active .chip-meta {
		opacity: 0.8;
	}
	.missing-years-note {
		margin: 0.5rem 0 0;
		font-size: 0.72rem;
		color: var(--color-text-muted);
	}

	.model-reference {
		margin-top: 0.75rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.35rem;
		overflow: visible;
	}
	.model-reference > summary {
		padding: 0.45rem 0.75rem;
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--color-text-dim);
		cursor: pointer;
		list-style: none;
		user-select: none;
		background: var(--color-surface);
		border-radius: 0.35rem;
		transition: background-color 0.12s;
	}
	.model-reference > summary::-webkit-details-marker {
		display: none;
	}
	.model-reference[open] > summary {
		border-bottom: 1px solid var(--color-border-subtle);
		border-radius: 0.35rem 0.35rem 0 0;
	}
	.model-reference > summary:hover {
		background: var(--color-accent-glow);
	}

	.model-ref-body {
		display: flex;
		flex-direction: column;
		gap: 0;
	}
	.model-ref-row {
		padding: 0.6rem 0.75rem;
		border-bottom: 1px solid var(--color-border-subtle);
		transition: background-color 0.1s;
	}
	.model-ref-row:last-child {
		border-bottom: none;
	}
	.model-ref-row.ref-selected {
		background: var(--color-accent-light);
	}

	.ref-header {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin-bottom: 0.2rem;
	}
	.ref-name {
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--color-text);
		min-width: 90px;
	}
	.ref-badge {
		font-size: 0.58rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		padding: 0.1rem 0.35rem;
		border-radius: 0.2rem;
	}
	.ref-badge-nwp {
		background: rgba(59, 130, 246, 0.15);
		color: #93c5fd;
	}
	.ref-badge-aiwp {
		background: rgba(52, 211, 153, 0.15);
		color: var(--color-status-complete);
	}
	.ref-forecast {
		font-size: 0.7rem;
		color: var(--color-text-muted);
	}
	.ref-res,
	.ref-period {
		font-size: 0.68rem;
		font-family: var(--font-mono);
		color: var(--color-text-muted);
		padding: 0.05rem 0.35rem;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: 0.2rem;
	}
	.ref-desc {
		margin: 0;
		font-size: 0.72rem;
		color: var(--color-text-muted);
		line-height: 1.4;
	}
</style>
