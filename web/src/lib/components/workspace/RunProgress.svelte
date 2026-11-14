<script lang="ts">
	import { cancelJob, failureMessage } from '$lib/api';
	import {
		cancellableJobIds,
		isUnfinished,
		modelProgress,
		type ModelState
	} from '$lib/workspace/run-lifecycle';
	import type { Run } from '$lib/workspace/runs';

	interface Props {
		run: Run;
	}

	let { run }: Props = $props();

	const TICK_MS = 15_000;
	const MARK: Record<ModelState, string> = {
		waiting: '○',
		running: '◐',
		cancelling: '◐',
		done: '✓',
		failed: '✕',
		cancelled: '–'
	};

	let now = $state(Date.now());
	let confirming = $state(false);
	let requested = $state(false);
	let cancelError = $state<string | null>(null);

	const models = $derived(modelProgress(run, now));
	const unfinished = $derived(isUnfinished(models));
	const stillRunning = $derived(
		models.filter((model) => model.state === 'waiting' || model.state === 'running').length
	);
	const cancellable = $derived(cancellableJobIds(run));
	// Stays "Canceling…" from the click until the run itself reports it has stopped.
	const cancelling = $derived(
		unfinished && (requested || models.some((model) => model.state === 'cancelling'))
	);

	$effect(() => {
		if (!unfinished) return;
		const timer = setInterval(() => (now = Date.now()), TICK_MS);
		return () => clearInterval(timer);
	});

	async function cancelRun() {
		confirming = false;
		requested = true;
		cancelError = null;
		const outcomes = await Promise.allSettled(cancellable.map((id) => cancelJob(id)));
		const refused = outcomes.find((outcome) => outcome.status === 'rejected');
		if (refused) {
			requested = false;
			cancelError = `This run could not be canceled: ${failureMessage(refused.reason)}`;
		}
	}
</script>

<section class="progress" aria-label="Run progress">
	{#if stillRunning > 0}
		<p class="note">
			{stillRunning}
			{stillRunning === 1 ? 'model is' : 'models are'} still running. Results appear as each one finishes.
		</p>
	{/if}
	<ul>
		{#each models as model (model.key)}
			<li class={model.state}>
				<span class="mark" aria-hidden="true">{MARK[model.state]}</span>
				<span class="name">{model.name}</span>
				<span class="state">{model.timing ? `${model.label} · ${model.timing}` : model.label}</span>
				{#if model.error}<span class="detail">{model.error}</span>{/if}
			</li>
		{/each}
	</ul>

	{#if unfinished}
		<div class="actions">
			{#if cancelling}
				<p class="note" role="status">Canceling… this can take a moment.</p>
			{:else if confirming}
				<p class="note">Cancel this run? Models that have already finished keep their results.</p>
				<button type="button" class="danger" onclick={cancelRun}>Yes, cancel run</button>
				<button type="button" onclick={() => (confirming = false)}>Keep running</button>
			{:else if cancellable.length > 0}
				<button type="button" onclick={() => (confirming = true)}>Cancel run</button>
			{/if}
		</div>
	{/if}
	{#if cancelError}<p class="error" role="alert">{cancelError}</p>{/if}
</section>

<style>
	.progress {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-3);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
		font-size: var(--text-size-label);
	}
	ul {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	li {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0 var(--space-2);
	}
	.mark {
		flex: none;
		width: 1em;
		text-align: center;
	}
	.name {
		font-weight: 600;
	}
	.state {
		color: var(--color-text-muted);
	}
	.detail {
		flex: 1 1 100%;
		padding-left: calc(1em + var(--space-2));
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
		overflow-wrap: anywhere;
	}
	.running .mark,
	.cancelling .mark {
		color: var(--color-status-running);
	}
	.done .mark {
		color: var(--color-status-complete);
	}
	.failed .mark,
	.failed .state {
		color: var(--color-status-failed);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
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
	button.danger {
		border-color: var(--color-danger);
		color: var(--color-danger);
	}
	.error {
		margin: 0;
		color: var(--color-danger);
	}
</style>
