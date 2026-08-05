<script lang="ts" module>
	export type RunStatus = 'running' | 'complete' | 'failed' | 'canceled' | 'mixed';

	export interface RunListItem {
		id: string;
		title: string;
		meta: string;
		count: number;
		status: RunStatus;
		canDelete: boolean;
	}

	export interface RunSection {
		title: string;
		items: RunListItem[];
		open?: boolean;
		emptyLabel?: string;
	}
</script>

<script lang="ts">
	import { onMount } from 'svelte';

	let {
		newLabel,
		newActive = false,
		selectedId,
		sections,
		onNew,
		onSelect,
		onDelete,
		deleteTitle = 'Delete'
	}: {
		newLabel: string;
		newActive?: boolean;
		selectedId: string | null;
		sections: RunSection[];
		onNew: () => void;
		onSelect: (id: string) => void;
		onDelete: (id: string) => void;
		deleteTitle?: string;
	} = $props();

	// A layout preference worth remembering: collapsed frees ~15rem for the
	// results and the assistant. The expand control stays on screen, so a
	// remembered collapse can always be undone.
	const COLLAPSED_KEY = 'almanac.runListCollapsed';
	let collapsed = $state(false);

	onMount(() => {
		collapsed = localStorage.getItem(COLLAPSED_KEY) === '1';
	});

	function setCollapsed(next: boolean) {
		collapsed = next;
		localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0');
	}
</script>

{#if collapsed}
	<aside class="sidebar is-collapsed">
		<button
			class="rail-btn"
			title="Show the run list"
			aria-label="Show the run list"
			onclick={() => setCollapsed(false)}>»</button
		>
		<button class="rail-btn" title={newLabel} aria-label={newLabel} onclick={onNew}>+</button>
	</aside>
{:else}
	<aside class="sidebar">
		<div class="sidebar-head">
			<button class="new-run-btn" class:active={newActive} onclick={onNew}>{newLabel}</button>
			<button
				class="rail-btn"
				title="Hide the run list"
				aria-label="Hide the run list"
				onclick={() => setCollapsed(true)}>«</button
			>
		</div>

		{#each sections as section}
			<details class="sidebar-section" open={section.open ?? true}>
				<summary class="sidebar-title">
					{section.title}
					<span class="sidebar-count">{section.items.length}</span>
				</summary>
				{#if section.items.length > 0}
					<ul class="run-list">
						{#each section.items as item (item.id)}
							<li class="run-list-item">
								<button
									class="run-item"
									class:selected={selectedId === item.id}
									onclick={() => onSelect(item.id)}
								>
									<div class="run-main">
										<span class="run-title">{item.title}</span>
										<span class="run-meta">{item.meta}</span>
									</div>
									<div class="run-side">
										<span class="model-count">{item.count}</span>
										<span class="status-dot {item.status}" title={item.status}></span>
									</div>
								</button>
								{#if item.canDelete}
									<button
										class="run-delete"
										title={deleteTitle}
										onclick={(e) => {
											e.stopPropagation();
											onDelete(item.id);
										}}>&times;</button
									>
								{/if}
							</li>
						{/each}
					</ul>
				{:else}
					<p class="sidebar-empty">{section.emptyLabel ?? 'Nothing here yet.'}</p>
				{/if}
			</details>
		{/each}
	</aside>
{/if}

<style>
	.sidebar {
		width: 14.5rem;
		flex-shrink: 0;
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
	}

	.sidebar.is-collapsed {
		width: auto;
		gap: 0.4rem;
	}

	.sidebar-head {
		display: flex;
		align-items: stretch;
		gap: 0.4rem;
	}

	.rail-btn {
		flex-shrink: 0;
		padding: 0.4rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.85rem;
		line-height: 1;
		cursor: pointer;
	}
	.rail-btn:hover {
		color: var(--color-accent);
		border-color: var(--color-accent);
		background: var(--color-accent-light);
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

	.run-list {
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

	.run-list-item {
		position: relative;
		display: flex;
		align-items: stretch;
		border-bottom: 1px solid var(--color-border-subtle);
	}

	.run-list-item:last-child {
		border-bottom: 0;
	}

	.run-item {
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
	.run-item:hover {
		background: color-mix(in srgb, var(--color-accent-light) 24%, transparent);
	}
	.run-item.selected {
		background: var(--color-accent-light);
		box-shadow: inset 0.14rem 0 0 var(--color-accent);
	}
	.run-item.selected .run-title {
		color: var(--color-accent);
	}

	.run-main {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.12rem;
	}

	.run-title {
		overflow: hidden;
		font-size: 0.82rem;
		font-weight: 750;
		line-height: 1.15;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.run-meta {
		overflow: hidden;
		font-size: 0.66rem;
		color: var(--color-text-muted);
		line-height: 1.2;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.run-side {
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

	.run-delete {
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
	.run-delete:hover {
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
	.status-dot.canceled {
		background: var(--color-text-muted);
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
