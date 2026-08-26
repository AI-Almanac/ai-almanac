<script lang="ts">
	import type { SetupWizardState } from '$lib/setup/wizard.svelte';
	import { saveStorage } from '$lib/api/setup';

	interface Props {
		wizard: SetupWizardState;
	}
	const { wizard }: Props = $props();

	let outputDir = $state('');
	let mountRoots = $state('');
	let initialized = false;
	$effect(() => {
		if (!initialized) {
			const m = wizard.state?.dataset_mount_roots as string[] | undefined;
			mountRoots = (m ?? []).join('\n');
			initialized = true;
		}
	});
	let saving = $state(false);

	async function save() {
		saving = true;
		wizard.storageError = null;
		try {
			await saveStorage({
				output_dir: outputDir.trim() || null,
				dataset_mount_roots: mountRoots
					.split('\n')
					.map((l: string) => l.trim())
					.filter((l: string) => Boolean(l))
			});
			wizard.goNext();
		} catch (e) {
			wizard.storageError = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

<div class="card">
	<h2>Storage</h2>
	<p class="lede">
		Configure where AI Almanac stores job outputs and where it looks for model datasets.
	</p>

	<div class="fields">
		<div class="field">
			<label for="output-dir">Job output directory</label>
			<p class="desc">
				Where benchmark results are written. Leave blank to use the default inside the data
				directory.
			</p>
			<input
				id="output-dir"
				type="text"
				bind:value={outputDir}
				placeholder="Leave blank for default"
			/>
		</div>

		<div class="field">
			<label for="mount-roots">Dataset mount roots</label>
			<p class="desc">
				Directories where model datasets are mounted (one path per line). These are the roots that
				users can register as local data sources.
			</p>
			<textarea id="mount-roots" rows="4" bind:value={mountRoots} placeholder="/mnt/data"
			></textarea>
		</div>
	</div>

	{#if wizard.storageError}
		<p class="error">{wizard.storageError}</p>
	{/if}

	<div class="actions">
		<button class="secondary" onclick={() => wizard.goPrev()}>← Back</button>
		<button onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save & continue →'}</button>
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
	.fields {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	label {
		font-weight: 600;
		font-size: 0.9rem;
	}
	.desc {
		margin: 0;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}
	input,
	textarea {
		padding: 0.45rem 0.6rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: 0.85rem;
		width: 100%;
		box-sizing: border-box;
	}
	textarea {
		resize: vertical;
		font-family: var(--font-mono, ui-monospace, monospace);
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
</style>
