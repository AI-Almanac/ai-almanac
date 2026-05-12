<script lang="ts">
	import { EVENT_TYPES } from '$lib/data/event-types';
	import type { Region } from '$lib/api';

	interface Props {
		region: Region;
		onSelect: (eventTypeId: string) => void;
		onBack: () => void;
	}

	const { region, onSelect, onBack }: Props = $props();
</script>

<header class="detail-header">
	<div class="detail-header-row">
		<button class="back-btn" onclick={onBack} title="Back to region selection">← Back</button>
		<div>
			<p class="detail-eyebrow">New Benchmark · Step 2 of 3</p>
			<h1 class="detail-title">Choose an Event Type</h1>
			<p class="detail-subtitle">
				Select the type of weather event to benchmark for <strong>{region.display_name}</strong>.
			</p>
		</div>
	</div>
</header>
<hr class="divider" />

<div class="card-grid">
	{#each EVENT_TYPES as et}
		<button
			type="button"
			class="picker-card"
			class:coming-soon={et.status === 'coming_soon'}
			disabled={et.status === 'coming_soon'}
			onclick={() => onSelect(et.id)}
		>
			<div class="card-top">
				<span class="card-name">{et.name}</span>
				{#if et.status === 'coming_soon'}
					<span class="badge-soon">Coming soon</span>
				{:else}
					<span class="badge-available">Available</span>
				{/if}
			</div>
			<p class="card-desc">{et.description}</p>
			{#if et.definition}
				<p class="card-definition">
					<span class="definition-label">Definition</span>{et.definition}
				</p>
			{/if}
			<div class="card-regions">
				{#each et.regions as r}
					<span class="region-chip">{r}</span>
				{/each}
			</div>
		</button>
	{/each}
</div>

<style>
	.detail-header {
		margin-bottom: 1.25rem;
	}
	.detail-header-row {
		display: flex;
		align-items: flex-start;
		gap: 1rem;
	}
	.detail-eyebrow {
		font-size: 0.65rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--color-accent);
		margin: 0 0 0.2rem;
	}
	.detail-title {
		font-size: 1.75rem;
		font-weight: 400;
		font-family: var(--font-display);
		margin: 0;
		color: var(--color-text);
	}
	.detail-subtitle {
		font-size: 0.85rem;
		color: var(--color-text-muted);
		margin: 0.35rem 0 0;
		max-width: 52ch;
		line-height: 1.5;
	}
	.back-btn {
		padding: 0.35rem 0.7rem;
		background: none;
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		color: var(--color-text-muted);
		font-family: var(--font-body);
		font-size: 0.78rem;
		font-weight: 500;
		cursor: pointer;
		transition:
			color 0.12s,
			border-color 0.12s;
		white-space: nowrap;
		margin-top: 0.2rem;
	}
	.back-btn:hover {
		color: var(--color-accent);
		border-color: var(--color-accent);
	}
	.divider {
		border: none;
		border-top: 1px solid var(--color-border-subtle);
		margin: 0 0 1.5rem;
	}

	.card-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 1rem;
	}

	.picker-card {
		display: flex;
		flex-direction: column;
		gap: 0.65rem;
		padding: 1.25rem 1.25rem 1rem;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		text-align: left;
		cursor: pointer;
		transition:
			border-color 0.15s,
			background-color 0.15s,
			box-shadow 0.15s,
			transform 0.1s;
		font-family: var(--font-body);
	}
	.picker-card:not(.coming-soon):hover {
		border-color: var(--color-accent);
		background: var(--color-surface-raised);
		box-shadow:
			0 0 0 3px var(--color-accent-glow),
			0 4px 16px rgba(0, 0, 0, 0.2);
		transform: translateY(-2px);
	}
	.picker-card.coming-soon {
		opacity: 0.45;
		cursor: default;
	}

	.card-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
	}
	.card-name {
		font-family: var(--font-display);
		font-size: 1.1rem;
		font-weight: 400;
		color: var(--color-text);
	}
	.card-desc {
		margin: 0;
		font-size: 0.78rem;
		color: var(--color-text-muted);
		line-height: 1.5;
		flex: 1;
	}
	.card-definition {
		margin: 0;
		font-size: 0.72rem;
		color: var(--color-text-muted);
		line-height: 1.4;
		padding: 0.4rem 0.6rem;
		background: var(--color-bg);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.3rem;
	}
	.definition-label {
		font-weight: 700;
		color: var(--color-accent);
		margin-right: 0.4rem;
		font-size: 0.62rem;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}
	.card-regions {
		display: flex;
		gap: 0.35rem;
		flex-wrap: wrap;
		margin-top: auto;
	}
	.region-chip {
		font-size: 0.68rem;
		font-weight: 500;
		padding: 0.15rem 0.5rem;
		border-radius: 1rem;
		background: var(--color-accent-light);
		color: var(--color-accent);
		border: 1px solid var(--color-accent-border);
	}
	.coming-soon .region-chip {
		background: var(--color-border-subtle);
		color: var(--color-text-dim);
		border-color: transparent;
	}

	.badge-available,
	.badge-soon {
		font-size: 0.6rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		padding: 0.15rem 0.45rem;
		border-radius: 0.25rem;
		white-space: nowrap;
		flex-shrink: 0;
	}
	.badge-available {
		background: var(--color-status-complete-bg);
		color: var(--color-status-complete);
	}
	.badge-soon {
		background: var(--color-border-subtle);
		color: var(--color-text-muted);
	}
</style>
