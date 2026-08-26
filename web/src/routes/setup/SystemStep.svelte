<script lang="ts">
	import type { SetupWizardState } from '$lib/setup/wizard.svelte';

	interface Props {
		wizard: SetupWizardState;
	}
	const { wizard }: Props = $props();

	type GpuInfo = { name?: string; memory_mib?: number; count?: number } | null | undefined;
	type PlatformInfo = { platform?: string; machine?: string } | undefined;

	const setupState = $derived(wizard.state);
	const platform = $derived(setupState?.platform as PlatformInfo);
	const gpu = $derived(setupState?.gpu as GpuInfo);
	const dataDir = $derived(setupState?.data_dir ?? '—');
</script>

<div class="card">
	<h2>System information</h2>
	<p class="lede">Review the environment that AI Almanac will use.</p>

	<dl class="facts">
		<div>
			<dt>Platform</dt>
			<dd>{platform?.platform ?? '—'}</dd>
		</div>
		<div>
			<dt>Architecture</dt>
			<dd>{platform?.machine ?? '—'}</dd>
		</div>
		<div>
			<dt>GPU</dt>
			<dd>
				{gpu
					? `${gpu.name ?? 'unknown'} (${gpu.memory_mib ?? '?'} MiB × ${gpu.count ?? 1})`
					: 'None detected'}
			</dd>
		</div>
		<div class="full">
			<dt>Data directory</dt>
			<dd><code>{dataDir}</code></dd>
		</div>
	</dl>

	<p class="hint">
		The data directory is set by the <code>AI_ALMANAC_DATA_DIR</code> environment variable. It stores
		the database, config, and job outputs.
	</p>

	<div class="actions">
		<button onclick={() => wizard.goNext()}>Next: Storage →</button>
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
	.facts {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.75rem 1.5rem;
		margin: 0;
	}
	.facts .full {
		grid-column: 1 / -1;
	}
	.facts div {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.facts dt {
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-muted);
	}
	.facts dd {
		margin: 0;
		font-size: 0.9rem;
		font-weight: 600;
	}
	code {
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.82em;
		padding: 0.1rem 0.3rem;
		border-radius: 0.25rem;
		background: var(--color-surface);
	}
	.hint {
		margin: 0;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}
	.actions {
		display: flex;
		justify-content: flex-end;
	}
	button {
		padding: 0.5rem 1.1rem;
		border-radius: 0.45rem;
		border: none;
		background: var(--color-accent);
		color: #fff;
		font: inherit;
		font-weight: 600;
		font-size: 0.9rem;
		cursor: pointer;
	}
	button:hover {
		opacity: 0.9;
	}
</style>
