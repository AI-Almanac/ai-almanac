<script lang="ts">
	import type { ModelConfig } from '$lib/api';

	interface Props {
		modelId: string;
		cfg: ModelConfig | undefined;
		getOverride: <T>(modelId: string, key: string, fallback: T) => T;
		setOverride: (modelId: string, key: string, value: string | boolean | number) => void;
	}

	const { modelId, cfg, getOverride, setOverride }: Props = $props();

	// Mirrors the server guardrail defaults (services/guardrails.py). Advisory
	// only — the server enforces the real checks at submit time.
	const SMALL_SAMPLE_SEASONS = 10;
	const PRESATELLITE_END_YEAR = 1978;

	function yearOf(date: string): number | null {
		const year = Number(date.slice(0, 4));
		return Number.isInteger(year) && year > 0 ? year : null;
	}

	const startYear = $derived(yearOf(getOverride(modelId, 'start_date', cfg?.start_date ?? '')));
	const endYear = $derived(yearOf(getOverride(modelId, 'end_date', cfg?.end_date ?? '')));
	const seasons = $derived(
		startYear !== null && endYear !== null && endYear >= startYear
			? endYear - startYear + 1
			: null
	);
	const smallSample = $derived(seasons !== null && seasons < SMALL_SAMPLE_SEASONS);
	const presatellite = $derived(startYear !== null && startYear <= PRESATELLITE_END_YEAR);
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
				>Evaluation Start <span class="tip" data-tip="First date included for this model.">ⓘ</span
				></span
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
				>Evaluation End <span class="tip" data-tip="Last date included for this model.">ⓘ</span
				></span
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
				>Baseline Start Year <span
					class="tip"
					data-tip="First year used to build the baseline seasonal behavior.">ⓘ</span
				></span
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
				>Baseline End Year <span
					class="tip"
					data-tip="Last year used to build the baseline seasonal behavior.">ⓘ</span
				></span
			>
			<input
				type="number"
				value={getOverride(modelId, 'end_year_clim', cfg?.end_year_clim ?? '')}
				oninput={(e) => setOverride(modelId, 'end_year_clim', (e.target as HTMLInputElement).value)}
			/>
		</label>
		<label
			><span class="label-text"
				>Initialization Days <span
					class="tip"
					data-tip="Comma-separated forecast initialization days within each week. '0,3' = Mon/Thu."
					>ⓘ</span
				></span
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
					>Run Years Concurrently <span
						class="tip"
						data-tip="Run years concurrently for faster results.">ⓘ</span
					></span
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
				>Ensemble Forecast <span
					class="tip"
					data-tip="Compute ensemble metrics in addition to deterministic metrics.">ⓘ</span
				></span
			>
		</label>
		{#if getOverride(modelId, 'probabilistic', cfg?.probabilistic ?? false)}
			<label
				><span class="label-text"
					>Ensemble Members <span
						class="tip"
						data-tip="Number of ensemble members to use. Enter a count (e.g. '11', '51') or 'All'."
						>ⓘ</span
					></span
				>
				<input
					value={getOverride(modelId, 'members', cfg?.members ?? '')}
					placeholder="e.g. 11 or All"
					oninput={(e) => setOverride(modelId, 'members', (e.target as HTMLInputElement).value)}
				/>
			</label>
		{/if}
	</div>
	{#if seasons !== null}
		<p class="season-note" class:warn={smallSample || presatellite}>
			Scores {seasons} onset season{seasons === 1 ? '' : 's'}.
			{#if smallSample}
				Under {SMALL_SAMPLE_SEASONS} seasons the scores are noisy — differences between models may
				not be real.
			{/if}
			{#if presatellite}
				Includes pre-satellite years (up to {PRESATELLITE_END_YEAR}), where AI-model skill is
				understated.
			{/if}
		</p>
	{/if}
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

	.season-note {
		margin: 0.6rem 0 0;
		font-size: 0.78rem;
		color: var(--color-text-muted);
	}

	.season-note.warn {
		color: var(--color-status-running);
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
	.tip {
		position: relative;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 0.9rem;
		height: 0.9rem;
		font-size: 0.58rem;
		font-style: normal;
		font-weight: 600;
		color: var(--color-text-muted);
		border: 1px solid var(--color-border);
		border-radius: 50%;
		cursor: help;
		line-height: 1;
		transition:
			color 0.12s,
			border-color 0.12s;
		flex-shrink: 0;
		vertical-align: middle;
	}
	.tip:hover {
		color: var(--color-accent);
		border-color: var(--color-accent);
	}
	.tip::after {
		content: attr(data-tip);
		position: absolute;
		bottom: calc(100% + 6px);
		left: 50%;
		transform: translateX(-50%);
		width: 220px;
		padding: 0.45rem 0.6rem;
		background: var(--color-surface-raised);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.35rem;
		font-size: 0.72rem;
		font-weight: 400;
		color: var(--color-text-muted);
		line-height: 1.45;
		white-space: normal;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
		pointer-events: none;
		opacity: 0;
		transition: opacity 0.15s;
		z-index: 1000;
		text-align: left;
	}
	.tip:hover::after {
		opacity: 1;
	}
</style>
