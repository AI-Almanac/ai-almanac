<script lang="ts">
	import { failureMessage } from '$lib/api';
	import { deletableJobIds } from '$lib/workspace/run-lifecycle';
	import { RUN_KIND_LABEL, runKind, type Run } from '$lib/workspace/runs';

	interface Props {
		run: Run;
		onDelete: (jobIds: string[]) => Promise<void>;
	}

	let { run, onDelete }: Props = $props();

	let confirming = $state(false);
	let deleting = $state(false);
	let deleteError = $state<string | null>(null);

	const jobIds = $derived(deletableJobIds(run));
	const kind = $derived(RUN_KIND_LABEL[runKind(run)].toLowerCase());

	async function deleteRun() {
		deleting = true;
		deleteError = null;
		try {
			await onDelete(jobIds);
		} catch (e) {
			deleteError = `This ${kind} could not be deleted: ${failureMessage(e)}`;
		} finally {
			deleting = false;
			confirming = false;
		}
	}
</script>

{#if jobIds.length > 0}
	<div class="delete-run">
		{#if deleting}
			<p class="note" role="status">Deleting…</p>
		{:else if confirming}
			<p class="note">
				Delete this {kind} and its files? It goes from every workspace, not only this one.
			</p>
			<button type="button" class="danger" onclick={deleteRun}>Yes, delete</button>
			<button type="button" onclick={() => (confirming = false)}>Keep it</button>
		{:else}
			<button type="button" onclick={() => (confirming = true)}>Delete {kind}</button>
		{/if}
		{#if deleteError}<p class="error" role="alert">{deleteError}</p>{/if}
	</div>
{/if}

<style>
	.delete-run {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
		font-size: var(--text-size-label);
	}
	.note {
		margin: 0;
		color: var(--color-text-muted);
	}
	button {
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		cursor: pointer;
	}
	button:hover {
		background: var(--color-surface-muted);
	}
	button.danger {
		border-color: var(--color-danger);
		color: var(--color-danger);
	}
	.error {
		flex: 1 1 100%;
		margin: 0;
		color: var(--color-danger);
		text-align: right;
	}
</style>
