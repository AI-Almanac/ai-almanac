<script lang="ts">
	import { EVENT_TYPES } from '$lib/data/event-types';
	import type { BenchmarkStore } from '$lib/benchmarks.svelte';

	interface Props {
		store: BenchmarkStore;
		onNewBenchmark: () => void;
		onSelectGroup: (key: string) => void;
	}

	const { store, onNewBenchmark, onSelectGroup }: Props = $props();

	function eventLabel(eventType: string): string {
		return EVENT_TYPES.find((event) => event.id === eventType)?.name ?? eventType;
	}

	function formatRunDate(value: string): string {
		if (!value) return 'Unknown date';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return new Intl.DateTimeFormat(undefined, {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		}).format(date);
	}

	function groupStatus(group: (typeof store.runGroups)[number]): string {
		if (group.jobs.some((job) => job.status === 'running')) return 'running';
		if (group.jobs.every((job) => job.status === 'complete')) return 'complete';
		if (group.jobs.every((job) => job.status === 'failed')) return 'failed';
		return 'mixed';
	}
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
							<div class="group-main">
								<span class="group-region">{group.region}</span>
								<span class="group-meta">
									{formatRunDate(group.mostRecentAt)} · {eventLabel(group.eventType)}
								</span>
							</div>
							<div class="group-side">
								<span class="model-count">{group.jobs.length}</span>
								<span class="status-dot {groupStatus(group)}" title={groupStatus(group)}></span>
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
		width: 14.5rem;
		flex-shrink: 0;
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
	}

	.new-run-btn {
		width: 100%;
		padding: 0.65rem 0.75rem;
		background: var(--color-accent);
		color: white;
		border: none;
		border-radius: 0.4rem;
		font-family: var(--font-body);
		font-size: 0.82rem;
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
		padding: 0.2rem 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		background: transparent;
		border-top: 1px solid var(--color-border-subtle);
		border-bottom: 1px solid var(--color-border-subtle);
		border-radius: 0;
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
		min-height: 2.85rem;
		padding: 0.42rem 1.5rem 0.42rem 0.55rem;
		border-radius: 0.25rem;
		border: none;
		background: none;
		cursor: pointer;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.55rem;
		transition:
			background-color 0.12s,
			box-shadow 0.12s;
		color: var(--color-text);
	}
	.group-item:hover {
		background: color-mix(in srgb, var(--color-accent-light) 24%, transparent);
	}
	.group-item.selected {
		background: var(--color-accent-light);
		box-shadow: inset 0.14rem 0 0 var(--color-accent);
	}
	.group-item.selected .group-region {
		color: var(--color-accent);
	}

	.group-main {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.12rem;
	}

	.group-region {
		overflow: hidden;
		font-size: 0.82rem;
		font-weight: 750;
		line-height: 1.15;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.group-meta {
		overflow: hidden;
		font-size: 0.66rem;
		color: var(--color-text-muted);
		line-height: 1.2;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.group-side {
		flex-shrink: 0;
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		color: var(--color-text-muted);
	}

	.model-count {
		min-width: 1.1rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 999rem;
		padding: 0.08rem 0.3rem;
		background: var(--color-surface);
		font-size: 0.62rem;
		font-weight: 800;
		line-height: 1.2;
		text-align: center;
	}

	.group-delete {
		position: absolute;
		top: 50%;
		right: 0.28rem;
		transform: translateY(-50%);
		padding: 0.1rem 0.22rem;
		background: none;
		border: none;
		color: var(--color-text-dim);
		font-size: 0.72rem;
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

	.status-dot {
		width: 0.45rem;
		height: 0.45rem;
		border-radius: 999rem;
		background: var(--color-text-muted);
	}

	.status-dot.running {
		background: var(--color-status-running-bg);
		box-shadow: inset 0 0 0 0.12rem var(--color-status-running);
	}

	.status-dot.complete {
		background: var(--color-status-complete);
	}

	.status-dot.failed {
		background: var(--color-status-failed);
	}

	.status-dot.mixed {
		background: var(--color-text-muted);
	}

	@media (max-width: 1050px) {
		.sidebar {
			width: 100%;
		}
	}
</style>
