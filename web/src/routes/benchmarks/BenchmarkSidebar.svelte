<script lang="ts">
	import { EVENT_TYPES } from '$lib/data/event-types';
	import type { BenchmarkStore } from '$lib/benchmarks.svelte';

	interface Props {
		store: BenchmarkStore;
		onNewBenchmark: () => void;
		onSelectGroup: (key: string) => void;
	}

	const { store, onNewBenchmark, onSelectGroup }: Props = $props();
</script>

<aside class="sidebar">
	<button class="new-run-btn" class:active={store.showForm} onclick={onNewBenchmark}>
		New benchmark
	</button>

	{#if store.runGroups.length > 0}
		{@const myGroups = store.runGroups.filter((g) => g.isOwner)}
		{@const sharedGroups = store.runGroups.filter((g) => !g.isOwner)}

		{#snippet groupList(groups: typeof store.runGroups)}
			<ul class="group-list">
				{#each groups as group}
					<li class="group-list-item">
						<button
							class="group-item"
							class:selected={store.selectedGroupKey === group.key && !store.showForm}
							onclick={() => onSelectGroup(group.key)}
						>
							<span class="group-event-type"
								>{EVENT_TYPES.find((e) => e.id === group.eventType)?.shortName ??
									group.eventType}</span
							>
							<span class="group-region">{group.region}</span>
							{#if group.startDate && group.endDate}
								<span class="group-dates">{group.startDate} – {group.endDate}</span>
							{/if}
							<div class="group-badges">
								<span class="badge-count"
									>{group.jobs.length} model{group.jobs.length !== 1 ? 's' : ''}</span
								>
								{#if group.jobs.some((j) => j.status === 'running')}
									<span class="status-badge running">running</span>
								{:else if group.jobs.every((j) => j.status === 'complete')}
									<span class="status-badge complete">complete</span>
								{:else if group.jobs.every((j) => j.status === 'failed')}
									<span class="status-badge failed">failed</span>
								{:else}
									<span class="status-badge mixed">mixed</span>
								{/if}
							</div>
						</button>
						{#if group.isOwner}
							<button
								class="group-delete"
								title="Delete run set"
								onclick={(e) => {
									e.stopPropagation();
									store.deleteGroup(group.key);
								}}>&times;</button
							>
						{/if}
					</li>
				{/each}
			</ul>
		{/snippet}

		<details class="sidebar-section" open>
			<summary class="sidebar-title"
				>My Benchmarks <span class="sidebar-count">{myGroups.length}</span></summary
			>
			{#if myGroups.length > 0}
				{@render groupList(myGroups)}
			{:else}
				<p class="sidebar-empty">No benchmarks yet.</p>
			{/if}
		</details>

		{#if sharedGroups.length > 0}
			<details class="sidebar-section">
				<summary class="sidebar-title"
					>Shared With Me <span class="sidebar-count">{sharedGroups.length}</span></summary
				>
				{@render groupList(sharedGroups)}
			</details>
		{/if}
	{/if}
</aside>

<style>
	.sidebar {
		width: 17rem;
		flex-shrink: 0;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.new-run-btn {
		width: 100%;
		padding: 0.75rem 0.9rem;
		background: var(--color-accent);
		color: white;
		border: none;
		border-radius: 0.4rem;
		font-family: var(--font-body);
		font-size: 0.875rem;
		font-weight: 600;
		cursor: pointer;
		transition:
			background-color 0.12s,
			transform 0.1s;
	}
	.new-run-btn:hover,
	.new-run-btn.active {
		background: var(--color-accent-hover);
		transform: translateY(-1px);
	}

	.sidebar-section {
		margin-top: 0;
	}
	.sidebar-section > summary {
		cursor: pointer;
		list-style: none;
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.15rem 0.05rem 0.5rem;
		border-radius: 0.3rem;
		user-select: none;
		border-bottom: 1px solid var(--color-border-subtle);
	}
	.sidebar-section > summary:hover {
		color: var(--color-text);
	}
	.sidebar-section > summary::before {
		content: '▶';
		font-size: 0.5rem;
		color: var(--color-text-muted);
		transition: transform 0.15s;
		flex-shrink: 0;
	}
	.sidebar-section[open] > summary::before {
		transform: rotate(90deg);
	}

	.sidebar-title {
		font-size: 0.78rem;
		font-weight: 750;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-text-muted);
		margin: 0;
	}
	.sidebar-count {
		font-size: 0.6rem;
		font-weight: 500;
		color: var(--color-text-muted);
		opacity: 0.7;
	}
	.sidebar-empty {
		font-size: 0.75rem;
		color: var(--color-text-muted);
		padding: 0.4rem 0.5rem;
		opacity: 0.6;
	}

	.group-list {
		list-style: none;
		padding: 0.25rem;
		margin: 0;
		display: flex;
		flex-direction: column;
		background: var(--color-surface);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.6rem;
	}

	.group-list-item {
		position: relative;
		display: flex;
		align-items: stretch;
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.group-list-item:last-child {
		border-bottom: 0;
	}

	.group-item {
		width: 100%;
		text-align: left;
		padding: 0.7rem 1.65rem 0.7rem 0.8rem;
		border-radius: 0.4rem;
		border: none;
		background: none;
		cursor: pointer;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		transition:
			background-color 0.12s,
			box-shadow 0.12s;
		color: var(--color-text);
	}
	.group-item:hover {
		background: color-mix(in srgb, var(--color-accent-light) 32%, transparent);
	}
	.group-item.selected {
		background: var(--color-accent-light);
		box-shadow: inset 0.18rem 0 0 var(--color-accent);
	}
	.group-item.selected .group-region {
		color: var(--color-accent);
	}

	.group-event-type {
		font-size: 0.64rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-accent);
	}
	.group-region {
		font-size: 0.92rem;
		font-weight: 650;
		line-height: 1.2;
	}
	.group-dates {
		font-size: 0.7rem;
		color: var(--color-text-muted);
		font-family: var(--font-mono);
	}

	.group-badges {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		margin-top: 0.05rem;
		flex-wrap: wrap;
	}

	.badge-count {
		font-size: 0.68rem;
		font-weight: 650;
		color: var(--color-text-muted);
	}

	.group-delete {
		position: absolute;
		top: 0.55rem;
		right: 0.45rem;
		padding: 0.15rem 0.3rem;
		background: none;
		border: none;
		color: var(--color-text-dim);
		font-size: 0.75rem;
		line-height: 1;
		cursor: pointer;
		border-radius: 0.2rem;
		transition:
			color 0.12s,
			background-color 0.12s;
	}
	.group-delete:hover {
		color: var(--color-danger);
		background-color: var(--color-danger-bg);
	}

	.status-badge {
		font-family: var(--font-mono);
		font-size: 0.66rem;
		font-weight: 500;
		padding: 0.12rem 0.45rem;
		border-radius: 0.25rem;
	}
	.status-badge.running {
		background: var(--color-status-running-bg);
		color: var(--color-status-running);
	}
	.status-badge.complete {
		background: var(--color-status-complete-bg);
		color: var(--color-status-complete);
	}
	.status-badge.failed {
		background: var(--color-status-failed-bg);
		color: var(--color-status-failed);
	}
	.status-badge.mixed {
		background: var(--color-border-subtle);
		color: var(--color-text-muted);
	}

	@media (max-width: 1050px) {
		.sidebar {
			width: 100%;
		}
	}
</style>
