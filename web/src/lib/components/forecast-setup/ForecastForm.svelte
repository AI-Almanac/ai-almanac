<script lang="ts">
	import {
		createForecast,
		type Blend,
		type Forecast,
		type ForecastModel,
		type InitSource
	} from '$lib/api';
	import {
		canSubmit,
		forecastRequest,
		runnableModels,
		unlinkedModels,
		type ForecastFields
	} from './forecast-form';

	interface Props {
		/** The blends a forecast can be run from: the finished ones. */
		blends: Blend[];
		forecastModels: ForecastModel[];
		initSources: InitSource[];
		/** The blend the form opens on, when its host has an obvious one. */
		startBlendId?: string;
		/** The models picked to begin with; ones the opening blend cannot run are dropped. */
		startModelNames?: string[];
		onSubmitted: (forecast: Forecast) => void | Promise<void>;
		onCancel: () => void;
	}

	let {
		blends,
		forecastModels,
		initSources,
		startBlendId = '',
		startModelNames = [],
		onSubmitted,
		onCancel
	}: Props = $props();

	// Where the form opens is only a starting point, so later changes to it are not followed.
	// svelte-ignore state_referenced_locally
	let blendId = $state(startBlendId);
	// svelte-ignore state_referenced_locally
	let modelNames = $state<string[]>(startModelNames);
	let initSource = $state('gfs');
	let initTime = $state('');
	// A number input binds a number, or undefined while it is empty.
	let maxIssueDates = $state<number | undefined>(undefined);
	let submitting = $state(false);
	let submitError = $state<string | null>(null);

	const blend = $derived(blends.find((candidate) => candidate.id === blendId) ?? null);
	const runnable = $derived(runnableModels(blend, forecastModels));
	const unlinked = $derived(unlinkedModels(blend));
	const fields = $derived<ForecastFields>({
		blendId,
		modelNames,
		initSource,
		initTime,
		maxIssueDates
	});
	const valid = $derived(canSubmit(fields, blend));

	// A model picked for one blend is dropped when the blend chosen next does not have it.
	$effect(() => {
		const names = new Set(runnable.map((entry) => entry.name));
		if (modelNames.some((name) => !names.has(name))) {
			modelNames = modelNames.filter((name) => names.has(name));
		}
	});

	function toggleModel(name: string) {
		modelNames = modelNames.includes(name)
			? modelNames.filter((picked) => picked !== name)
			: [...modelNames, name];
	}

	async function submit() {
		if (!valid || submitting) return;
		submitting = true;
		submitError = null;
		try {
			await onSubmitted(await createForecast(forecastRequest(fields)));
		} catch (err) {
			submitError = err instanceof Error ? err.message : String(err);
		} finally {
			submitting = false;
		}
	}
</script>

<div class="form">
	<label class="field" data-tour="forecast-blend">
		<span>Blend</span>
		<select bind:value={blendId}>
			<option value="" disabled>Select a completed blend…</option>
			{#each blends as candidate (candidate.id)}
				<option value={candidate.id}>{candidate.name || 'Untitled blend'}</option>
			{/each}
		</select>
		{#if blendId === '' && blends.length === 0}
			<p class="muted">No completed blends yet. Train one first.</p>
		{/if}
	</label>

	<label class="field" data-tour="forecast-init">
		<span>Initialization data source</span>
		<select bind:value={initSource}>
			{#each initSources as source (source.id)}
				<option value={source.id}>{source.display_name}</option>
			{/each}
		</select>
		<p class="muted">The conditions each live rollout is initialized from.</p>
	</label>

	{#if blend}
		<fieldset class="field" data-tour="forecast-models">
			<legend>Forecast models</legend>
			{#if unlinked.length > 0}
				<p class="muted">
					Every model in a blend needs a live forecast model before the blend can forecast. Not
					linked yet: {unlinked.join(', ')}. Choose one for each in its data source settings.
				</p>
			{/if}
			{#if runnable.length > 0}
				<div class="model-grid">
					{#each runnable as entry (entry.name)}
						<label class="checkbox">
							<input
								type="checkbox"
								checked={modelNames.includes(entry.name)}
								onchange={() => toggleModel(entry.name)}
							/>
							<span>{entry.model.display_name}</span>
						</label>
					{/each}
				</div>
			{/if}
		</fieldset>

		<details class="advanced">
			<summary>Advanced</summary>
			<label class="field">
				<span>Init time (UTC)</span>
				<input type="text" bind:value={initTime} placeholder="defaults to latest available" />
			</label>
			<label class="field">
				<span>Max season issue dates</span>
				<input
					type="number"
					min="1"
					bind:value={maxIssueDates}
					placeholder="defaults to the whole season-to-date"
				/>
			</label>
		</details>
	{/if}

	{#if submitError}
		<p class="error" role="alert">{submitError}</p>
	{/if}

	<div class="form-actions">
		<button type="button" class="ghost" onclick={onCancel}>Cancel</button>
		<button
			type="button"
			class="primary"
			data-tour="forecast-run"
			disabled={!valid || submitting}
			onclick={submit}
		>
			{submitting ? 'Submitting…' : 'Run forecast'}
		</button>
	</div>
</div>

<style>
	.form {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.muted {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.field > span,
	.field legend {
		font-size: 0.8rem;
		font-weight: 700;
		color: var(--color-text);
	}
	.field input,
	.field select {
		padding: 0.55rem 0.65rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-bg);
		color: var(--color-text);
		font: inherit;
	}
	fieldset.field {
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.45rem;
		padding: 0.75rem;
	}
	.model-grid {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem 1.25rem;
	}
	.checkbox {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.9rem;
		color: var(--color-text);
	}
	.advanced summary {
		cursor: pointer;
		font-weight: 700;
		font-size: 0.85rem;
		color: var(--color-text);
	}
	.advanced[open] {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		margin-top: 0.5rem;
	}
	.form-actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.6rem;
	}
	button.primary,
	button.ghost {
		border-radius: 0.4rem;
		padding: 0.55rem 0.9rem;
		font-weight: 750;
		cursor: pointer;
		border: 1px solid var(--color-border);
	}
	button.primary {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: white;
	}
	button.primary:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	button.ghost {
		background: var(--color-surface);
		color: var(--color-text);
	}
	.error {
		margin: 0;
		color: var(--color-danger);
		font-size: 0.85rem;
	}
</style>
