<script lang="ts">
	import InfoTip from '$lib/components/InfoTip.svelte';
	import type { ModelConfig } from '$lib/api';

	interface Props {
		modelId: string;
		cfg: ModelConfig | undefined;
		getOverride: <T>(modelId: string, key: string, fallback: T) => T;
		setOverride: (modelId: string, key: string, value: string | boolean | number) => void;
	}

	const { modelId, cfg, getOverride, setOverride }: Props = $props();
</script>

<fieldset class="nested-fieldset">
	<div class="model-fieldset-header">
		<span class="model-fieldset-name">{cfg?.display_name ?? modelId}</span>
		{#if cfg}
			<span class="model-avail-range"
				>available {cfg.start_date.slice(0, 7)} – {cfg.end_date.slice(0, 7)}</span
			>
		{/if}
	</div>
	<div class="field-row">
		<label
			><span class="label-text"
				>Evaluation Start <InfoTip text="First date included for this model." /></span
			>
			<input
				type="date"
				value={getOverride(modelId, 'start_date', cfg?.start_date ?? '')}
				oninput={(e) => setOverride(modelId, 'start_date', (e.target as HTMLInputElement).value)}
				required
			/>
		</label>
		<label
			><span class="label-text"
				>Evaluation End <InfoTip text="Last date included for this model." /></span
			>
			<input
				type="date"
				value={getOverride(modelId, 'end_date', cfg?.end_date ?? '')}
				oninput={(e) => setOverride(modelId, 'end_date', (e.target as HTMLInputElement).value)}
				required
			/>
		</label>
		<label
			><span class="label-text"
				>Baseline Start Year <InfoTip
					text="First year used to build the baseline seasonal behavior."
				/></span
			>
			<input
				type="number"
				value={getOverride(modelId, 'start_year_clim', cfg?.start_year_clim ?? '')}
				oninput={(e) =>
					setOverride(modelId, 'start_year_clim', (e.target as HTMLInputElement).value)}
			/>
		</label>
		<label
			><span class="label-text"
				>Baseline End Year <InfoTip
					text="Last year used to build the baseline seasonal behavior."
				/></span
			>
			<input
				type="number"
				value={getOverride(modelId, 'end_year_clim', cfg?.end_year_clim ?? '')}
				oninput={(e) => setOverride(modelId, 'end_year_clim', (e.target as HTMLInputElement).value)}
			/>
		</label>
		<label
			><span class="label-text"
				>Initialization Days <InfoTip
					text="Comma-separated forecast initialization days within each week. '0,3' = Mon/Thu."
				/></span
			>
			<input
				value={getOverride(modelId, 'init_days', cfg?.init_days ?? '')}
				oninput={(e) => setOverride(modelId, 'init_days', (e.target as HTMLInputElement).value)}
			/>
		</label>
		{#if !getOverride(modelId, 'probabilistic', cfg?.probabilistic ?? false)}
			<label class="checkbox-label">
				<input
					type="checkbox"
					checked={getOverride(modelId, 'parallel', true)}
					onchange={(e) => setOverride(modelId, 'parallel', (e.target as HTMLInputElement).checked)}
				/>
				<span class="label-text"
					>Run Years Concurrently <InfoTip
						text="Run years concurrently for faster results."
					/></span
				>
			</label>
		{/if}
		<label class="checkbox-label">
			<input
				type="checkbox"
				checked={getOverride(modelId, 'probabilistic', cfg?.probabilistic ?? false)}
				onchange={(e) =>
					setOverride(modelId, 'probabilistic', (e.target as HTMLInputElement).checked)}
			/>
			<span class="label-text"
				>Ensemble Forecast <InfoTip
					text="Compute ensemble metrics in addition to deterministic metrics."
				/></span
			>
		</label>
		{#if getOverride(modelId, 'probabilistic', cfg?.probabilistic ?? false)}
			<label
				><span class="label-text"
					>Ensemble Members <InfoTip
						text="Number of ensemble members to use. Enter a count (e.g. '11', '51') or 'All'."
					/></span
				>
				<input
					value={getOverride(modelId, 'members', cfg?.members ?? '')}
					placeholder="e.g. 11 or All"
					oninput={(e) => setOverride(modelId, 'members', (e.target as HTMLInputElement).value)}
				/>
			</label>
		{/if}
	</div>
</fieldset>

<style>
	.nested-fieldset {
		border: none;
		border-top: 1px solid var(--color-border-subtle);
		border-radius: 0;
		padding: 0.75rem 0.9rem;
		margin: 0;
	}
	.nested-fieldset:first-of-type {
		border-top: none;
	}

	.model-fieldset-header {
		display: flex;
		align-items: baseline;
		gap: 0.6rem;
		margin-bottom: 0.65rem;
	}
	.model-fieldset-name {
		font-size: 0.72rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-muted);
	}
	.model-avail-range {
		font-size: 0.65rem;
		font-family: var(--font-mono);
		color: var(--color-text-dim);
	}

	.field-row {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		align-items: flex-end;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--color-text);
	}
	label.checkbox-label {
		flex-direction: row;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.8rem;
		padding-bottom: 0.15rem;
	}

	input:not([type]),
	input[type='number'],
	input[type='date'] {
		padding: 0.4rem 0.6rem;
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		background: var(--color-bg);
		color: var(--color-text);
		font-family: var(--font-body);
		font-size: 0.82rem;
		outline: none;
		transition:
			border-color 0.15s,
			box-shadow 0.15s;
	}
	input:focus {
		border-color: var(--color-accent);
		box-shadow: 0 0 0 3px var(--color-accent-light);
	}

	.label-text {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
	}
</style>
