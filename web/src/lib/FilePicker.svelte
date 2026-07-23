<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { fsList, fsQuickPaths, type FsEntry, type QuickPath } from './api';

	interface Props {
		open: boolean;
		mode?: 'directory' | 'file';
		initialPath?: string;
		title?: string;
		onclose: () => void;
		onselect: (path: string) => void;
	}

	let {
		open = $bindable(false),
		mode = 'directory',
		initialPath = '',
		title = mode === 'directory' ? 'Choose a directory' : 'Choose a file',
		onclose,
		onselect
	}: Props = $props();

	let cwd = $state('');
	let entries = $state<FsEntry[]>([]);
	let parent = $state<string | null>(null);
	let quickPaths = $state<QuickPath[]>([]);
	let showHidden = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let focusedIndex = $state(-1);
	let listEl = $state<HTMLUListElement | null>(null);

	$effect(() => {
		if (open) {
			loadQuickPaths();
			navigate(initialPath || '');
		}
	});

	async function loadQuickPaths() {
		try {
			quickPaths = await fsQuickPaths();
		} catch {
			quickPaths = [];
		}
	}

	async function navigate(target: string) {
		loading = true;
		error = null;
		focusedIndex = -1;
		try {
			const res = await fsList(target, showHidden);
			cwd = res.path;
			parent = res.parent;
			entries = res.entries;
			await tick();
			listEl?.scrollTo({ top: 0 });
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	function pathSegments(p: string): { label: string; path: string }[] {
		const parts = p.split('/').filter(Boolean);
		const segs: { label: string; path: string }[] = [{ label: '/', path: '/' }];
		let cum = '';
		for (const part of parts) {
			cum += '/' + part;
			segs.push({ label: part, path: cum });
		}
		return segs;
	}

	function onEntryClick(entry: FsEntry) {
		if (entry.kind === 'dir') {
			navigate(cwd.replace(/\/$/, '') + '/' + entry.name);
		} else if (mode === 'file') {
			onselect(cwd.replace(/\/$/, '') + '/' + entry.name);
			onclose();
		}
	}

	function selectCurrent() {
		if (mode === 'directory') {
			onselect(cwd);
			onclose();
		}
	}

	function onKey(e: KeyboardEvent) {
		if (!open) return;
		if (e.key === 'Escape') {
			e.preventDefault();
			onclose();
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			focusedIndex = Math.min(focusedIndex + 1, entries.length - 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			focusedIndex = Math.max(focusedIndex - 1, 0);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (focusedIndex >= 0 && entries[focusedIndex]) {
				onEntryClick(entries[focusedIndex]);
			} else if (mode === 'directory') {
				selectCurrent();
			}
		} else if (e.key === 'Backspace' && parent) {
			// Up one level when not typing in an input.
			const t = e.target as HTMLElement;
			if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
			e.preventDefault();
			navigate(parent);
		}
	}

	function visibleEntries(): FsEntry[] {
		if (mode === 'directory') return entries.filter((e) => e.kind === 'dir');
		return entries;
	}
</script>

<svelte:window onkeydown={onKey} />

{#if open}
	<button class="backdrop" type="button" onclick={onclose} aria-label="Close"></button>
	<div class="modal" role="dialog" aria-modal="true" aria-label={title}>
		<header>
			<h2>{title}</h2>
			<button class="close" onclick={onclose} aria-label="Close">×</button>
		</header>

		<div class="crumbs">
			{#each pathSegments(cwd) as seg, i (i + seg.path)}
				{#if i > 0}<span class="sep">/</span>{/if}
				<button class="crumb" onclick={() => navigate(seg.path)}>{seg.label}</button>
			{/each}
		</div>

		<div class="body">
			<aside>
				<h3>Shortcuts</h3>
				<ul>
					{#each quickPaths as q (q.path)}
						<li>
							<button onclick={() => navigate(q.path)}>{q.label}</button>
						</li>
					{/each}
				</ul>
				<label class="hidden-toggle">
					<input type="checkbox" bind:checked={showHidden} onchange={() => navigate(cwd)} />
					Show hidden
				</label>
			</aside>

			<section class="list">
				{#if error}
					<div class="banner err">{error}</div>
				{:else if loading}
					<p class="muted">Loading…</p>
				{:else if visibleEntries().length === 0}
					<p class="muted">Empty directory.</p>
				{:else}
					<ul bind:this={listEl}>
						{#if parent}
							<li>
								<button class="entry" onclick={() => navigate(parent!)}>
									<span class="icon">↑</span>
									<span class="name">..</span>
								</button>
							</li>
						{/if}
						{#each visibleEntries() as entry, i (entry.name)}
							<li>
								<button
									class="entry"
									class:focused={focusedIndex === i}
									onclick={() => onEntryClick(entry)}
								>
									<span class="icon">{entry.kind === 'dir' ? '📁' : '📄'}</span>
									<span class="name">{entry.name}</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		</div>

		<footer>
			<div class="cwd">
				<label>
					<span>Path</span>
					<input
						type="text"
						bind:value={cwd}
						onkeydown={(e) => e.key === 'Enter' && navigate(cwd)}
					/>
				</label>
			</div>
			<div class="footer-actions">
				<button class="ghost" onclick={onclose}>Cancel</button>
				{#if mode === 'directory'}
					<button class="primary" onclick={selectCurrent}>Select this directory</button>
				{/if}
			</div>
		</footer>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.45);
		z-index: 100;
	}
	.modal {
		position: fixed;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		width: min(100% - 2rem, 56rem);
		height: min(100% - 4rem, 36rem);
		background: var(--color-surface-raised);
		border: 1px solid var(--color-border);
		border-radius: 0.8rem;
		z-index: 101;
		display: grid;
		grid-template-rows: auto auto 1fr auto;
		overflow: hidden;
		box-shadow: 0 1.5rem 3rem rgba(0, 0, 0, 0.3);
	}
	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.85rem 1.2rem;
		border-bottom: 1px solid var(--color-border);
	}
	header h2 {
		margin: 0;
		font-size: 1rem;
	}
	.close {
		background: transparent;
		border: none;
		color: var(--color-text-muted);
		font-size: 1.3rem;
		cursor: pointer;
		padding: 0 0.5rem;
	}
	.crumbs {
		display: flex;
		gap: 0.3rem;
		align-items: center;
		flex-wrap: wrap;
		padding: 0.6rem 1.2rem;
		border-bottom: 1px solid var(--color-border);
		background: var(--color-surface);
	}
	.crumb {
		background: transparent;
		border: none;
		color: var(--color-text);
		padding: 0.2rem 0.45rem;
		border-radius: 0.3rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.85rem;
		cursor: pointer;
	}
	.crumb:hover {
		background: var(--color-surface-raised);
	}
	.sep {
		color: var(--color-text-muted);
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.85rem;
	}
	.body {
		display: grid;
		grid-template-columns: 12rem 1fr;
		overflow: hidden;
	}
	aside {
		border-right: 1px solid var(--color-border);
		padding: 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		overflow-y: auto;
	}
	aside h3 {
		margin: 0;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--color-text-muted);
	}
	aside ul {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	aside ul button {
		width: 100%;
		text-align: left;
		background: transparent;
		border: none;
		color: var(--color-text);
		padding: 0.35rem 0.5rem;
		border-radius: 0.3rem;
		cursor: pointer;
	}
	aside ul button:hover {
		background: var(--color-surface);
	}
	.hidden-toggle {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.8rem;
		color: var(--color-text-muted);
		margin-top: auto;
		padding-top: 0.5rem;
		border-top: 1px solid var(--color-border);
	}
	.list {
		overflow-y: auto;
		padding: 0.5rem;
	}
	.list ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.entry {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		width: 100%;
		text-align: left;
		background: transparent;
		border: none;
		color: var(--color-text);
		padding: 0.4rem 0.6rem;
		border-radius: 0.3rem;
		cursor: pointer;
		font-family: inherit;
	}
	.entry:hover,
	.entry.focused {
		background: var(--color-surface);
	}
	.entry .name {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.88rem;
	}
	.icon {
		width: 1.2rem;
		text-align: center;
	}
	footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.85rem 1.2rem;
		border-top: 1px solid var(--color-border);
		background: var(--color-surface);
	}
	.cwd label {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.75rem;
		color: var(--color-text-muted);
	}
	.cwd input {
		padding: 0.4rem 0.6rem;
		border-radius: 0.35rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface-raised);
		color: var(--color-text);
		font: inherit;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.85rem;
		min-width: 18rem;
	}
	.footer-actions {
		display: flex;
		gap: 0.6rem;
	}
	footer button {
		padding: 0.5rem 1rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	.ghost {
		background: transparent;
		color: var(--color-text-muted);
	}
	.primary {
		background: var(--color-text);
		color: var(--color-bg);
	}
	.banner.err {
		padding: 0.55rem 0.8rem;
		border-radius: 0.4rem;
		background: color-mix(in oklab, var(--color-danger, #c33) 12%, transparent);
		color: var(--color-danger, #c33);
		border: 1px solid color-mix(in oklab, var(--color-danger, #c33) 30%, transparent);
		font-size: 0.85rem;
	}
	.muted {
		color: var(--color-text-muted);
		padding: 1rem;
	}
</style>
