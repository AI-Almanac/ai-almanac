<script lang="ts">
	import { failureMessage, listWorkspaces, type Workspace } from '$lib/api';
	import type { WorkspaceDescription } from '$lib/api/workspaces';
	import { overlay } from '$lib/workspace/overlay';
	import { FILTER_FROM, matchingWorkspaces } from '$lib/workspace/workspace-switcher';

	interface Props {
		/** The workspace open now, named on the trigger. */
		workspace: Pick<Workspace, 'id' | 'name' | 'description'>;
		onDescribe: (change: WorkspaceDescription) => Promise<void>;
		/** Opens a workspace with nothing in it yet. */
		onStartBlank: () => Promise<void>;
	}

	let { workspace, onDescribe, onStartBlank }: Props = $props();

	let open = $state(false);
	let editing = $state(false);
	let opener = $state<HTMLButtonElement | null>(null);
	let listed = $state<Workspace[] | null>(null);
	let query = $state('');
	let problem = $state<string | null>(null);
	let busy = $state(false);
	let draftName = $state('');
	let draftDescription = $state('');

	const shown = $derived(matchingWorkspaces(listed ?? [], query));

	function close() {
		open = false;
		editing = false;
	}

	async function toggle() {
		if (open) return close();
		open = true;
		query = '';
		problem = null;
		try {
			listed = await listWorkspaces();
		} catch (e) {
			problem = failureMessage(e);
		}
	}

	function startEditing() {
		draftName = workspace.name;
		draftDescription = workspace.description;
		problem = null;
		editing = true;
	}

	async function run(action: () => Promise<void>) {
		busy = true;
		problem = null;
		try {
			await action();
			close();
		} catch (e) {
			problem = failureMessage(e);
		} finally {
			busy = false;
		}
	}

	function saveDescription(event: SubmitEvent) {
		event.preventDefault();
		if (!draftName.trim()) return;
		void run(() => onDescribe({ name: draftName.trim(), description: draftDescription.trim() }));
	}

	function focusFirst(node: HTMLElement) {
		node.focus();
	}
</script>

<span class="switcher">
	<h1>
		<button
			bind:this={opener}
			type="button"
			class="trigger"
			aria-haspopup="dialog"
			aria-expanded={open}
			title="Switch, rename or start a workspace"
			onclick={toggle}
		>
			<span class="ws-title-name">{workspace.name}</span>
			<span class="caret" aria-hidden="true">▾</span>
		</button>
	</h1>
	{#if open}
		<div
			class="popover"
			role="dialog"
			aria-label="Workspaces"
			use:overlay={{ onClose: close, opener, closeOnOutsideClick: true }}
		>
			{#if editing}
				<form class="describe" onsubmit={saveDescription}>
					<label class="field">
						<span class="label">Name</span>
						<input bind:value={draftName} maxlength="120" required use:focusFirst />
					</label>
					<label class="field">
						<span class="label">What it is for (optional)</span>
						<textarea
							bind:value={draftDescription}
							rows="3"
							maxlength="2000"
							placeholder="e.g. How well the AI models predict the Kiremt onset in 2026"></textarea>
					</label>
					{#if problem}<p class="problem" role="alert">{problem}</p>{/if}
					<div class="actions">
						<button type="submit" class="primary" disabled={busy || !draftName.trim()}>
							{busy ? 'Saving…' : 'Save'}
						</button>
						<button type="button" class="quiet" onclick={() => (editing = false)}>Cancel</button>
					</div>
				</form>
			{:else}
				{#if (listed?.length ?? 0) >= FILTER_FROM}
					<input
						class="filter"
						type="search"
						aria-label="Find a workspace"
						placeholder="Find a workspace"
						bind:value={query}
						use:focusFirst
					/>
				{/if}
				{#if listed === null && !problem}
					<p class="muted">Loading…</p>
				{:else}
					<ul class="list">
						{#each shown as ws (ws.id)}
							<li>
								<a
									href={`/workspace/${ws.id}`}
									class:current={ws.id === workspace.id}
									aria-current={ws.id === workspace.id ? 'page' : undefined}
									onclick={close}
								>
									<span class="ws-name">{ws.name}</span>
									{#if ws.description}<span class="muted">{ws.description}</span>{/if}
								</a>
							</li>
						{:else}
							<li class="muted">No workspace matches.</li>
						{/each}
					</ul>
				{/if}
				{#if problem}<p class="problem" role="alert">{problem}</p>{/if}
				<div class="actions">
					<button
						type="button"
						class="primary"
						disabled={busy}
						onclick={() => void run(onStartBlank)}
					>
						New workspace
					</button>
					<button type="button" class="quiet" onclick={startEditing}>
						Rename this workspace
					</button>
				</div>
			{/if}
		</div>
	{/if}
</span>

<style>
	.switcher {
		position: relative;
		display: inline-flex;
		min-width: 0;
	}
	.trigger {
		display: inline-flex;
		align-items: baseline;
		gap: var(--space-1);
		min-width: 0;
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}
	h1 {
		display: flex;
		min-width: 0;
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--text-size-title);
	}
	.ws-title-name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.trigger:hover .ws-title-name,
	.trigger:focus-visible .ws-title-name {
		text-decoration: underline;
	}
	.caret {
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
	.popover {
		position: absolute;
		top: calc(100% + var(--space-2));
		left: 0;
		z-index: 30;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		width: min(24rem, 90vw);
		padding: var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface-raised);
		box-shadow: var(--shadow-soft);
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		max-height: 50vh;
		margin: 0;
		padding: 0;
		overflow-y: auto;
		list-style: none;
	}
	.list a {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		padding: var(--space-2);
		border-radius: 0.35rem;
		color: inherit;
		text-decoration: none;
	}
	.list a:hover,
	.list a:focus-visible {
		background: var(--color-surface-muted);
	}
	.list a.current {
		background: var(--color-accent-soft, var(--color-surface-muted));
	}
	.ws-name {
		font-weight: 600;
	}
	.list .muted {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.muted {
		margin: 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-label);
	}
	.problem {
		margin: 0;
		color: var(--color-danger);
		font-size: var(--text-size-label);
	}
	.describe,
	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.describe {
		gap: var(--space-3);
	}
	.label {
		font-size: var(--text-size-label);
		font-weight: 600;
	}
	input,
	textarea {
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	textarea {
		resize: vertical;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
	}
	.primary {
		padding: var(--space-2) var(--space-4);
		border: 0;
		border-radius: 0.4rem;
		background: var(--color-accent);
		color: white;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	.primary:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.quiet {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		cursor: pointer;
	}
</style>
