<script lang="ts">
	import type { SetupWizardState } from '$lib/setup/wizard.svelte';
	import { finishSetup } from '$lib/api/setup';

	interface Props {
		wizard: SetupWizardState;
	}
	const { wizard }: Props = $props();

	let finishing = $state(false);
	let error = $state<string | null>(null);

	async function finish() {
		finishing = true;
		error = null;
		try {
			await finishSetup();
			// Full reload so /config.js re-evaluates setupRequired → false
			window.location.href = '/';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			finishing = false;
		}
	}
</script>

<div class="card">
	<h2>All set!</h2>
	<p class="lede">
		AI Almanac is configured and ready to use. Click the button below to finish setup and open the
		main interface.
	</p>

	<ul class="checklist">
		<li>✓ System checked</li>
		<li>✓ Storage configured</li>
		<li>✓ LLM endpoint saved</li>
		<li>
			{#if wizard.prepareStatus === 'done'}
				✓ Environments ready
			{:else if wizard.prepareStatus === 'running'}
				⟳ Environment install in progress
			{:else}
				— Environments (install later with <code>ai-almanac env prepare</code>)
			{/if}
		</li>
	</ul>

	{#if error}
		<p class="error">{error}</p>
	{/if}

	<div class="actions">
		<button class="secondary" onclick={() => wizard.goPrev()}>← Back</button>
		<button onclick={finish} disabled={finishing}>
			{finishing ? 'Finishing…' : 'Finish setup →'}
		</button>
	</div>
</div>

<style>
	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
		padding: 1.25rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	.lede {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}
	.checklist {
		margin: 0;
		padding: 0 0 0 0.25rem;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		font-size: 0.9rem;
	}
	code {
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.82em;
		padding: 0.1rem 0.3rem;
		border-radius: 0.25rem;
		background: var(--color-surface);
	}
	.error {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-status-failed, #c00);
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.75rem;
	}
	button {
		padding: 0.5rem 1.25rem;
		border-radius: 0.45rem;
		border: 1px solid var(--color-border);
		background: var(--color-accent);
		color: #fff;
		font: inherit;
		font-weight: 600;
		font-size: 0.9rem;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	button.secondary {
		background: transparent;
		color: var(--color-text-muted);
	}
</style>
