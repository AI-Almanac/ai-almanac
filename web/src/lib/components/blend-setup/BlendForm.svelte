<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import {
		createBlend,
		getRegions,
		type Blend,
		type BlendRunSpec,
		type DataSource,
		type Region
	} from '$lib/api';
	import { failureMessage } from '$lib/api/core';
	import FocusAreaMap from '$lib/components/FocusAreaMap.svelte';
	import InfoTip from '$lib/components/InfoTip.svelte';
	import { regionCountry } from '$lib/places';
	import {
		describeSourceCoverage,
		gridStep,
		gridsMatch,
		scoredTogether
	} from '$lib/source-coverage';
	import {
		blankBlendFields,
		blendRequest,
		fieldsFromSpec,
		hasRequiredFields,
		toggleModel,
		withObservations
	} from './blend-form';
	import {
		DAY_LEVEL_DEFAULTS,
		dayLevelFits,
		dayLevelParamsError,
		dayLevelRunNote
	} from './day-level-params';
	import { ONSET_DEFAULTS, onsetParamsError } from './onset-params';
	import {
		MIN_ONSET_YEARS,
		DEFAULT_MIN_TRAINING_YEARS,
		computeCoverage,
		coverageLimits,
		defaultSplit,
		memberCountWarning,
		parseYearSpec,
		yearSpecError
	} from './year-coverage';

	interface Props {
		/** Ready observation datasets to choose from. */
		obsSources: DataSource[];
		/**
		 * Ready forecast model datasets. Each carries the server's live-forecast
		 * verdict (source.live_forecast): whether a registry model exists for it and
		 * runs on the archive's grid.
		 */
		modelSources: DataSource[];
		/** Whether new blends train the day-level blend, so its settings are offered. */
		dayLevelAvailable?: boolean;
		/** The enforced minimum, so the prefilled split is one the server accepts. */
		minTrainingYears?: number;
		/** A setup to fill the fields with; each new one replaces what is in them. */
		startFrom?: BlendRunSpec | null;
		/** Shown above the fields. */
		heading?: Snippet;
		/** Training has started for this blend. */
		onSubmitted: (blend: Blend) => void | Promise<void>;
		onCancel: () => void;
	}

	let {
		obsSources,
		modelSources,
		dayLevelAvailable = false,
		minTrainingYears = DEFAULT_MIN_TRAINING_YEARS,
		startFrom = null,
		heading,
		onSubmitted,
		onCancel
	}: Props = $props();

	let fields = $state(blankBlendFields());
	let submitting = $state(false);
	let submitError = $state<string | null>(null);
	let regions = $state<Region[]>([]);

	// Only the country to pick places in by default; without it the user picks one.
	onMount(() => {
		getRegions()
			.then((listed) => (regions = listed))
			.catch(() => {});
	});

	const selectedObs = $derived(obsSources.find((s) => s.id === fields.obsDatasetId) ?? null);
	const country = $derived(regionCountry(regions, selectedObs?.region ?? null));
	const chosenModels = $derived(modelSources.filter((s) => fields.modelIds.includes(s.id)));

	// Only offer models that have data where the chosen observations do, on the
	// same grid.
	const availableModels = $derived(
		selectedObs
			? modelSources.filter(
					(s) => scoredTogether(selectedObs, s) && gridsMatch(gridStep(selectedObs), gridStep(s))
				)
			: []
	);

	// Drop any selected model that no longer matches the chosen observations.
	$effect(() => {
		const ids = new Set(availableModels.map((s) => s.id));
		if (fields.modelIds.some((id) => !ids.has(id))) {
			fields.modelIds = fields.modelIds.filter((id) => ids.has(id));
		}
	});

	const coverage = $derived(computeCoverage(selectedObs ?? undefined, chosenModels));
	const insufficientData = $derived(coverage != null && coverage.earliestForecast > coverage.end);
	const limits = $derived(
		coverage
			? coverageLimits(selectedObs ?? undefined, chosenModels, coverage)
			: { start: [], end: [] }
	);
	const limitingIds = $derived(new Set([...limits.start, ...limits.end].map((s) => s.id)));
	const limitsSummary = $derived(
		[
			limits.start.length ? `start set by ${limits.start.map((s) => s.name).join(', ')}` : '',
			limits.end.length ? `end set by ${limits.end.map((s) => s.name).join(', ')}` : ''
		]
			.filter(Boolean)
			.join('; ')
	);

	const yearError = $derived(
		yearSpecError(
			coverage,
			fields.trainingYears,
			fields.cvHoldoutYears,
			fields.forecastYears,
			fields.trueHoldoutYears
		)
	);

	// Prefill a working training/holdout split once obs + models are chosen, until
	// the years are chosen by hand or by a setup.
	$effect(() => {
		if (!coverage || fields.yearsChosen) return;
		const split = defaultSplit(coverage, minTrainingYears);
		if (!split) return;
		fields.trainingYears = split.training;
		fields.cvHoldoutYears = split.cv;
		fields.trueHoldoutYears = split.trueHoldout;
	});

	const onsetError = $derived(onsetParamsError(fields));
	const dayLevelError = $derived(dayLevelAvailable ? dayLevelParamsError(fields.dayLevel) : null);
	// What the chosen day-level cross-validation costs. Advisory only.
	const dayLevelNoteText = $derived.by(() => {
		const cvYears = parseYearSpec(fields.cvHoldoutYears.trim());
		if (!dayLevelAvailable || !cvYears?.length || dayLevelError) return null;
		const folds = Number(fields.dayLevel.yearsPerFold.trim() || DAY_LEVEL_DEFAULTS.years_per_fold);
		const trueHoldouts = parseYearSpec(fields.trueHoldoutYears.trim())?.length ?? 0;
		return dayLevelRunNote(dayLevelFits(cvYears.length, trueHoldouts, folds), folds);
	});

	// Advisory only — never feeds formValid.
	const modelCountWarning = $derived(memberCountWarning(fields.modelIds.length));

	// Live scoring needs a rolled-out season for every member, so one historical-only
	// member makes the whole blend historical-only. Said here, before training is paid
	// for, rather than only as a rejection at forecast time.
	const liveForecastBlockers = $derived(
		availableModels
			.filter((s) => fields.modelIds.includes(s.id) && s.live_forecast?.status !== 'ready')
			.map((s) => `${s.name}: ${s.live_forecast?.detail ?? 'No live forecast model is available.'}`)
	);

	const formValid = $derived(
		hasRequiredFields(fields) && !yearError && !onsetError && !dayLevelError && !insufficientData
	);

	// Mirror a setup produced elsewhere (the setup assistant) into the fields so the
	// two stay in sync — the user can hand off and keep editing fields directly.
	$effect(() => {
		if (startFrom) fields = fieldsFromSpec(startFrom);
	});

	function chooseObservations(id: string) {
		fields = withObservations(
			fields,
			obsSources.find((s) => s.id === id)
		);
	}

	function chooseYears() {
		fields.yearsChosen = true;
	}

	async function submit() {
		if (!formValid || submitting) return;
		submitting = true;
		submitError = null;
		try {
			const blend = await createBlend(blendRequest(fields, dayLevelAvailable));
			fields = blankBlendFields();
			await onSubmitted(blend);
		} catch (err) {
			submitError = failureMessage(err);
		} finally {
			submitting = false;
		}
	}
</script>

{#snippet fieldLabel(text: string, tip: string)}
	<span class="label-with-help">
		{text}
		<InfoTip text={tip} />
	</span>
{/snippet}

<div class="form">
	{@render heading?.()}

	<label class="field">
		{@render fieldLabel(
			'Blend name',
			'A label to identify this blend in your list. Does not affect training.'
		)}
		<input type="text" bind:value={fields.name} placeholder="e.g. India monsoon blend" />
	</label>

	<label class="field" data-tour="blend-obs">
		{@render fieldLabel(
			'Observations',
			'Ground-truth rainfall used both to score the forecasts and to build the onset climatology baseline. Earlier coverage allows earlier forecast years.'
		)}
		<select value={fields.obsDatasetId} onchange={(e) => chooseObservations(e.currentTarget.value)}>
			<option value="" disabled>Select an observation source…</option>
			{#each obsSources as source (source.id)}
				<option value={source.id}>{source.name}{source.region ? ` (${source.region})` : ''}</option>
			{/each}
		</select>
	</label>

	<fieldset class="field" data-tour="blend-models">
		<legend class="label-with-help">
			Forecast models
			<InfoTip
				text="Select two or more forecast models to combine. Training learns how much weight each model gets in the blend."
			/>
		</legend>
		{#if !selectedObs}
			<p class="muted">Select an observation source first to see matching models.</p>
		{:else if availableModels.length === 0}
			<p class="muted">
				No ready forecast models match these observations' region and grid. Add some under Data
				first.
			</p>
		{:else}
			<div class="model-grid">
				{#each availableModels as source (source.id)}
					<label class="checkbox">
						<input
							type="checkbox"
							checked={fields.modelIds.includes(source.id)}
							onchange={() => (fields = toggleModel(fields, source.id))}
						/>
						<span class="model-label">
							<span>{source.name}{source.region ? ` (${source.region})` : ''}</span>
							{#if describeSourceCoverage(source)}
								<small class="model-coverage" class:limits={limitingIds.has(source.id)}
									>{describeSourceCoverage(source)}</small
								>
							{/if}
						</span>
						{#if source.live_forecast?.status === 'ready'}
							<span class="forecast-badge" title="This model can also generate live forecasts."
								>Live forecast</span
							>
						{:else if source.live_forecast?.status === 'grid_mismatch'}
							<span class="forecast-badge blocked" title={source.live_forecast.detail}
								>Past seasons only</span
							>
						{/if}
					</label>
				{/each}
			</div>
			<p class="muted forecast-legend">
				<span class="forecast-badge">Live forecast</span> models can be extended into the current season.
				Any model can be blended and benchmarked — those without the badge cover past seasons only.
			</p>
		{/if}
	</fieldset>

	{#if modelCountWarning}
		<p class="caution">{modelCountWarning}</p>
	{/if}

	{#if liveForecastBlockers.length > 0}
		<p class="caution">
			Past seasons only: this blend can be trained and scored on past seasons, but not run as a live
			forecast for the current season.
			{liveForecastBlockers.join(' ')}
		</p>
	{/if}

	{#if coverage}
		<p class="muted coverage-hint">
			Shared data: {coverage.start}–{coverage.end}{limitsSummary ? ` (${limitsSummary})` : ''}.
			Forecast years start at {coverage.earliestForecast}: the climatology baseline is fitted only
			on observed years before the first forecast year. Grid cells with fewer than {MIN_ONSET_YEARS} onsets
			in those years are left out.
		</p>
	{/if}

	<div class="field-row" data-tour="blend-years">
		<label class="field">
			{@render fieldLabel(
				'Training years',
				`Years used to fit the blending weights, e.g. "2008:2010". The climatology baseline is fitted only on observed years before the first forecast year, so it never sees these years. Grid cells with fewer than ${MIN_ONSET_YEARS} onsets in those years are left out.`
			)}
			<input
				type="text"
				bind:value={fields.trainingYears}
				oninput={chooseYears}
				placeholder="2015:2020"
			/>
		</label>
		<label class="field">
			{@render fieldLabel(
				'Cross-validation years',
				'Cross-validation checks the blend on years it did not learn from: each year listed here is left out of training, the weights are fitted on the remaining years, and the left-out year is scored. Defaults to the training years, so every training year gets a turn being left out.'
			)}
			<input
				type="text"
				bind:value={fields.cvHoldoutYears}
				oninput={chooseYears}
				placeholder="2015:2020"
			/>
		</label>
		<label class="field">
			{@render fieldLabel(
				'True holdout years',
				`Years kept out of training and cross-validation entirely, so their scores show how the blend does on seasons it has never seen — the closest stand-in for a new season. Defaults to the most recent fifth of the shared years, as long as at least ${minTrainingYears} training years remain. Leave empty to train on every year.`
			)}
			<input
				type="text"
				bind:value={fields.trueHoldoutYears}
				oninput={chooseYears}
				placeholder="optional"
			/>
		</label>
	</div>

	{#if insufficientData}
		<p class="error">
			The climatology baseline needs at least {MIN_ONSET_YEARS} observed years before the first forecast
			year, and these sources don't leave that many before any year they share. Pick an observation source
			with earlier coverage.
		</p>
	{:else if yearError}
		<p class="error">{yearError}</p>
	{/if}

	<div class="field">
		{@render fieldLabel(
			'Area of interest',
			'Pick a place or draw a box to train and score the blend only on cells inside it. Leave empty to use the whole region.'
		)}
		<FocusAreaMap
			value={fields.focusArea}
			{country}
			onchange={(area) => (fields.focusArea = area)}
		/>
	</div>
	<details class="advanced">
		<summary>Advanced</summary>
		<div class="field-row">
			<label class="field">
				{@render fieldLabel(
					'Forecast years',
					'Years to stage and score, if different from training + holdout. Defaults to the union of training and holdout years.'
				)}
				<input
					type="text"
					bind:value={fields.forecastYears}
					placeholder="defaults to training + holdout"
				/>
			</label>
		</div>
		<label class="field">
			{@render fieldLabel(
				'Formula',
				'Advanced override for the statistical formula combining the models. Leave blank to use the default blend formula.'
			)}
			<input
				type="text"
				bind:value={fields.formulaText}
				placeholder="optional — model formula override"
			/>
		</label>

		<p class="muted advanced-group">
			Onset definition — leave blank to use the defaults ({ONSET_DEFAULTS.threshold_mm} mm,
			{ONSET_DEFAULTS.cutoff_month_day}, {ONSET_DEFAULTS.ref_onset_month_day}). Changing these
			redefines what counts as onset for both the observations and every model.
		</p>
		<div class="field-row" data-tour="blend-onset">
			<label class="field">
				{@render fieldLabel(
					'Onset rainfall threshold (mm)',
					`Total rainfall over 3 consecutive days that marks the start of the season. Default ${ONSET_DEFAULTS.threshold_mm} mm.`
				)}
				<input
					type="text"
					inputmode="decimal"
					bind:value={fields.thresholdMm}
					placeholder={String(ONSET_DEFAULTS.threshold_mm)}
				/>
			</label>
			<label class="field">
				{@render fieldLabel(
					'Onset search start (MM-DD)',
					`Calendar date from which onset is searched each season; also the first forecast issue date. Default ${ONSET_DEFAULTS.cutoff_month_day}.`
				)}
				<input
					type="text"
					bind:value={fields.cutoffMonthDay}
					placeholder={ONSET_DEFAULTS.cutoff_month_day}
				/>
			</label>
			<label class="field">
				{@render fieldLabel(
					'Reference onset date (MM-DD)',
					`Climatological onset date the "onset before reference" probability is scored against. Default ${ONSET_DEFAULTS.ref_onset_month_day}.`
				)}
				<input
					type="text"
					bind:value={fields.refOnsetMonthDay}
					placeholder={ONSET_DEFAULTS.ref_onset_month_day}
				/>
			</label>
		</div>
		{#if onsetError}
			<p class="error">{onsetError}</p>
		{/if}

		{#if dayLevelAvailable}
			<p class="muted advanced-group">
				Day-level blend — leave blank to use the defaults (one year out per fold,
				{DAY_LEVEL_DEFAULTS.trees} trees, no depth limit, {DAY_LEVEL_DEFAULTS.min_samples_leaf}
				rows per leaf, every row per tree).
			</p>
			<div class="field-row">
				<label class="field">
					{@render fieldLabel(
						'Years held out per fold',
						'CV holdout years held out together in each day-level fold, in contiguous blocks. 1 leaves one year out at a time, as the week-level blend does; more years per fold means fewer forests to fit.'
					)}
					<input
						type="text"
						inputmode="numeric"
						bind:value={fields.dayLevel.yearsPerFold}
						placeholder={String(DAY_LEVEL_DEFAULTS.years_per_fold)}
					/>
				</label>
				<label class="field">
					{@render fieldLabel('Trees', 'Trees in each day-level forest. Fewer trees train faster.')}
					<input
						type="text"
						inputmode="numeric"
						bind:value={fields.dayLevel.trees}
						placeholder={String(DAY_LEVEL_DEFAULTS.trees)}
					/>
				</label>
			</div>
			<div class="field-row">
				<label class="field">
					{@render fieldLabel(
						'Deepest tree',
						'Most levels a tree may grow. Leave blank for no limit; a limit trains faster.'
					)}
					<input
						type="text"
						inputmode="numeric"
						bind:value={fields.dayLevel.maxDepth}
						placeholder="no limit"
					/>
				</label>
				<label class="field">
					{@render fieldLabel(
						'Fewest rows per leaf',
						'A tree stops splitting where a leaf would hold fewer rows than this.'
					)}
					<input
						type="text"
						inputmode="numeric"
						bind:value={fields.dayLevel.minSamplesLeaf}
						placeholder={String(DAY_LEVEL_DEFAULTS.min_samples_leaf)}
					/>
				</label>
				<label class="field">
					{@render fieldLabel(
						'Share of rows per tree',
						'Share of the training rows each tree draws, from more than 0 up to 1. Smaller shares train faster.'
					)}
					<input
						type="text"
						inputmode="decimal"
						bind:value={fields.dayLevel.maxSamples}
						placeholder={String(DAY_LEVEL_DEFAULTS.max_samples)}
					/>
				</label>
			</div>
			{#if dayLevelError}
				<p class="error">{dayLevelError}</p>
			{:else if dayLevelNoteText}
				<p class="caution">{dayLevelNoteText}</p>
			{/if}
		{/if}
	</details>

	{#if submitError}
		<p class="error">{submitError}</p>
	{/if}

	<div class="form-actions">
		<button type="button" class="ghost" onclick={onCancel}>Cancel</button>
		<button
			type="button"
			class="primary"
			data-tour="blend-train"
			disabled={!formValid || submitting}
			onclick={submit}
		>
			{submitting ? 'Submitting…' : 'Train blend'}
		</button>
	</div>
</div>

<style>
	.muted {
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}

	.form {
		display: flex;
		flex-direction: column;
		gap: 1rem;
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

	.label-with-help {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
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
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr));
		gap: 0.4rem;
	}

	.checkbox {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.9rem;
		color: var(--color-text);
	}

	.model-label {
		display: flex;
		flex-direction: column;
	}

	.model-coverage {
		font-size: 0.8em;
		color: var(--color-text-muted);
	}

	.model-coverage.limits {
		color: var(--color-status-running);
		font-weight: 600;
	}

	.forecast-badge {
		font-size: 0.62rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.05rem 0.35rem;
		border-radius: 0.2rem;
		background: rgba(52, 211, 153, 0.15);
		color: var(--color-status-complete);
		white-space: nowrap;
	}

	.forecast-badge.blocked {
		background: rgba(251, 191, 36, 0.18);
		color: var(--color-status-warning, #b45309);
	}

	.forecast-legend {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.4rem;
		margin-top: 0.5rem;
	}

	.field-row {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.field-row .field {
		flex: 1;
		min-width: 12rem;
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
	}

	.advanced-group {
		margin: 0.5rem 0 -0.5rem;
		padding-top: 0.75rem;
		border-top: 1px solid var(--color-border-subtle);
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

	button.ghost:disabled {
		opacity: 0.55;
		cursor: not-allowed;
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
		color: var(--color-danger);
		font-size: 0.85rem;
	}

	.caution {
		color: var(--color-status-running);
		background: var(--color-status-running-bg);
		border: 1px solid var(--color-status-running);
		border-radius: 0.45rem;
		padding: 0.6rem 0.75rem;
		margin: 0;
		font-size: 0.85rem;
	}
</style>
