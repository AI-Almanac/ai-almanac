<script lang="ts">
	import { account } from '$lib/account.svelte';
	import { promoteJobToExample, unshareJob } from '$lib/api';

	interface Props {
		/** A completed job to promote, or null while nothing is promotable.
		 *  For a benchmark run group one id suffices: the server promotes the
		 *  group's completed siblings together. */
		promoteId: string | null;
		/** Every job to return to private on demote (unshare is per job). */
		demoteIds: string[];
		isExample: boolean;
		onChanged: () => void;
	}

	const { promoteId, demoteIds, isExample, onChanged }: Props = $props();

	let busy = $state(false);
	let error = $state<string | null>(null);

	async function promote() {
		if (!promoteId) return;
		const ok = confirm(
			'Feature this as the public example? Its results and logs become ' +
				'visible to everyone, including visitors who are not signed in.'
		);
		if (!ok) return;
		await run(() => promoteJobToExample(promoteId));
	}

	async function demote() {
		const ok = confirm(
			"Stop featuring this example? It returns to private and disappears from everyone else's lists."
		);
		if (!ok) return;
		await run(async () => {
			for (const id of demoteIds) await unshareJob(id);
		});
	}

	async function run(action: () => Promise<unknown>) {
		busy = true;
		error = null;
		try {
			await action();
			onChanged();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}
</script>

{#if account.isAdmin && (isExample || promoteId)}
	<button
		type="button"
		class="example-toggle"
		disabled={busy}
		onclick={isExample ? demote : promote}
	>
		{#if busy}Working…{:else if isExample}Stop featuring{:else}Feature as example{/if}
	</button>
	{#if error}
		<span class="example-error" title={error}>Couldn’t update example</span>
	{/if}
{/if}

<style>
	.example-toggle {
		padding: 0.45rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.82rem;
		font-weight: 600;
		cursor: pointer;
	}

	.example-toggle:hover:not(:disabled) {
		color: var(--color-accent);
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}

	.example-toggle:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.example-error {
		color: var(--color-danger);
		font-size: 0.75rem;
	}
</style>
