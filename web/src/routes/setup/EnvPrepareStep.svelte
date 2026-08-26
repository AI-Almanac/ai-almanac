<script lang="ts">
	import type { SetupWizardState } from '$lib/setup/wizard.svelte';
	import { startPrepare } from '$lib/api/setup';

	interface Props {
		wizard: SetupWizardState;
	}
	const { wizard }: Props = $props();

	type GpuInfo = { name?: string; memory_mib?: number; count?: number } | null | undefined;
	type PlatformInfo = { platform?: string; machine?: string } | undefined;

	const setupState = $derived(wizard.state);
	const gpu = $derived(setupState?.gpu as GpuInfo);
	const platform = $derived(setupState?.platform as PlatformInfo);

	// Default: include forecast only when GPU detected and Linux
	const defaultForecast = $derived(
		Boolean(gpu) && ((platform?.platform ?? '') as string).startsWith('linux')
	);
	let includeForecast = $state(false);
	$effect(() => {
		includeForecast = defaultForecast;
	});

	let starting = $state(false);
	let startError = $state<string | null>(null);

	let logEl = $state<HTMLElement | null>(null);

	// Auto-scroll log
	$effect(() => {
		wizard.prepareLog; // track dependency
		if (logEl) logEl.scrollTop = logEl.scrollHeight;
	});

	async function start() {
		starting = true;
		startError = null;
		try {
			const status = await startPrepare({ include_forecast: includeForecast });
			wizard.prepareStatus = status.status as 'idle' | 'running' | 'done' | 'failed';
			if (status.started || status.status === 'running') {
				void wizard.attachStream(-1);
			}
		} catch (e) {
			startError = e instanceof Error ? e.message : String(e);
		} finally {
			starting = false;
		}
	}

	const envEntries = $derived(Object.entries(wizard.envStatus));
	const statusLabel: Record<string, string> = {
		ready: '✓ ready',
		missing: '— not installed',
		partial: '◑ partial',
		unsupported: '— unsupported'
	};
</script>

<div class="card">
	<h2>Prepare environments</h2>
	<p class="lede">
		Install the Python environments needed to run benchmarks. This downloads pixi and installs
		packages — it may take several minutes.
	</p>

	{#if envEntries.length > 0}
		<div class="env-status">
			{#each envEntries as [name, envSt] (name)}
				<div class="env-row" class:ready={envSt === 'ready'}>
					<span class="env-name">{name}</span>
					<span class="env-badge" data-status={envSt}>{statusLabel[envSt] ?? envSt}</span>
				</div>
			{/each}
		</div>
	{/if}

	{#if wizard.prepareStatus === 'idle'}
		<div class="field">
			<label class="checkrow">
				<input type="checkbox" bind:checked={includeForecast} />
				<span>Include forecast environments</span>
			</label>
			<p class="desc">Forecast envs are large (~2 GB). Requires a Linux system.</p>
		</div>

		{#if startError}
			<p class="error">{startError}</p>
		{/if}

		<div class="actions">
			<button class="secondary" onclick={() => wizard.goPrev()}>← Back</button>
			<button onclick={start} disabled={starting}>
				{starting ? 'Starting…' : 'Install environments →'}
			</button>
			<button class="skip" onclick={() => wizard.goNext()}>Skip for now</button>
		</div>
	{:else}
		<div class="log-wrap">
			<div class="log-header">
				<span class="status-label" data-status={wizard.prepareStatus}>
					{#if wizard.prepareStatus === 'running'}⟳ Installing…
					{:else if wizard.prepareStatus === 'done'}✓ Complete
					{:else if wizard.prepareStatus === 'failed'}✗ Failed
					{:else}{wizard.prepareStatus}
					{/if}
				</span>
			</div>
			<pre bind:this={logEl} class="log">{wizard.prepareLog.join('\n')}</pre>
		</div>

		<div class="actions">
			{#if wizard.prepareStatus === 'done'}
				<button onclick={() => wizard.goNext()}>Continue →</button>
			{:else if wizard.prepareStatus === 'failed'}
				<button
					class="secondary"
					onclick={() => {
						wizard.prepareStatus = 'idle';
						wizard.prepareLog = [];
					}}
				>
					Retry
				</button>
				<button onclick={() => wizard.goNext()}>Skip & continue</button>
			{:else}
				<span class="running-hint">Please wait…</span>
				<button class="skip" onclick={() => wizard.goNext()}>Continue in background</button>
			{/if}
		</div>
	{/if}
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
	.env-status {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.env-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 0.4rem 0.6rem;
		border-radius: 0.35rem;
		background: var(--color-surface);
		font-size: 0.85rem;
	}
	.env-name {
		font-weight: 600;
	}
	.env-badge[data-status='ready'] {
		color: var(--color-accent);
	}
	.env-badge[data-status='missing'] {
		color: var(--color-text-muted);
	}
	.env-badge[data-status='failed'] {
		color: var(--color-status-failed, #c00);
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.checkrow {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-weight: 600;
		font-size: 0.9rem;
		cursor: pointer;
	}
	.desc {
		margin: 0;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}
	.error {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-status-failed, #c00);
	}
	.log-wrap {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.log-header {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.status-label {
		font-size: 0.85rem;
		font-weight: 600;
	}
	.status-label[data-status='running'] {
		color: var(--color-status-running, #1a73e8);
	}
	.status-label[data-status='done'] {
		color: var(--color-accent);
	}
	.status-label[data-status='failed'] {
		color: var(--color-status-failed, #c00);
	}
	.log {
		height: 240px;
		overflow-y: auto;
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		padding: 0.6rem;
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.75rem;
		line-height: 1.5;
		margin: 0;
		white-space: pre-wrap;
		word-break: break-all;
	}
	.running-hint {
		font-size: 0.85rem;
		color: var(--color-text-muted);
		align-self: center;
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.75rem;
		align-items: center;
	}
	button {
		padding: 0.5rem 1.1rem;
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
	button.skip {
		background: transparent;
		color: var(--color-text-muted);
		font-weight: 400;
		font-size: 0.85rem;
	}
</style>
