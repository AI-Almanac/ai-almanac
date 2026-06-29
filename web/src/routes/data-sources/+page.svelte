<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listDataSources,
		validateDataSource,
		createDataSource,
		updateDataSource,
		revalidateDataSource,
		deleteDataSource,
		getRegions,
		type DataSource,
		type DataSourceCreate,
		type DataSourceValidation,
		type Region
	} from '$lib/api';
	import { goto } from '$app/navigation';
	import DataCatalogNav from '$lib/DataCatalogNav.svelte';
	import DataCatalogPageHeader from '$lib/DataCatalogPageHeader.svelte';
	import FilePicker from '$lib/FilePicker.svelte';
	import { account } from '$lib/account.svelte';

	$effect(() => {
		if (account.loaded && !account.canManageData) goto('/');
	});

	let sources = $state<DataSource[]>([]);
	let regions = $state<Region[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let formKind = $state<'obs' | 'model'>('obs');
	let formName = $state('');
	let formPath = $state('');
	let formRegion = $state('');
	let formFilePattern = $state('{}.nc');
	let formVar = $state('RAINFALL');
	let formModelType = $state('AIWP');
	let formInitDays = $state('');
	let formInitDaysSource = $state('');
	let submitting = $state(false);
	let pickerOpen = $state(false);
	let editingId = $state<string | null>(null);
	let revalidatingId = $state<string | null>(null);
	let validationDraft = $state<DataSourceValidation | null>(null);
	let validatedSignature = $state('');

	async function load() {
		loading = true;
		error = null;
		try {
			[sources, regions] = await Promise.all([listDataSources(), getRegions()]);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function formMetadata(): Record<string, unknown> {
		if (formKind === 'obs') {
			return {
				...(formFilePattern && { obs_file_pattern: formFilePattern }),
				...(formVar && { obs_var: formVar })
			};
		}
		return {
			...(formVar && { model_var: formVar }),
			...(formModelType && { model_type: formModelType }),
			...(formFilePattern && { file_pattern: formFilePattern }),
			...(formInitDays && { init_days: formInitDays }),
			...(formInitDaysSource && { init_days_source: formInitDaysSource })
		};
	}

	function formBody(): DataSourceCreate {
		return {
			kind: formKind,
			name: formName.trim(),
			path: formPath.trim(),
			region: formRegion,
			metadata: formMetadata()
		};
	}

	function formSignature(): string {
		const body = formBody();
		const metadata = { ...body.metadata };
		delete metadata.init_days;
		delete metadata.init_days_source;
		return JSON.stringify({ ...body, metadata });
	}

	const validationIsCurrent = $derived(
		validationDraft !== null && validatedSignature === formSignature()
	);

	async function onSubmit(event: Event) {
		event.preventDefault();
		if (editingId) {
			await saveEdit();
			return;
		}
		await validateDraft();
	}

	async function validateDraft() {
		submitting = true;
		error = null;
		try {
			const draft = await validateDataSource(formBody());
			validationDraft = draft;
			formPath = draft.path;
			if (formKind === 'model') {
				formInitDays = String(draft.metadata.init_days ?? '');
				formInitDaysSource = String(draft.metadata.init_days_source ?? '');
			}
			validatedSignature = formSignature();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			submitting = false;
		}
	}

	async function confirmAdd() {
		if (!validationDraft || !validationIsCurrent || validationDraft.status !== 'ready') return;
		submitting = true;
		error = null;
		try {
			const metadata = { ...validationDraft.metadata };
			if (formKind === 'model') {
				const inferredDays = String(validationDraft.metadata.init_days ?? '');
				metadata.init_days = formInitDays;
				if (formInitDaysSource === 'configured' || formInitDays !== inferredDays) {
					metadata.init_days_source = 'configured';
					delete metadata.init_time_coordinate;
					delete metadata.init_time_sample_count;
				}
			}
			await createDataSource({ ...formBody(), metadata });
			resetForm();
			await load();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			submitting = false;
		}
	}

	async function saveEdit() {
		if (!editingId) return;
		submitting = true;
		error = null;
		try {
			const { kind: _kind, ...body } = formBody();
			await updateDataSource(editingId, body);
			resetForm();
			await load();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			submitting = false;
		}
	}

	function resetForm() {
		editingId = null;
		formKind = 'obs';
		formName = '';
		formPath = '';
		formRegion = '';
		formFilePattern = '{}.nc';
		formVar = 'RAINFALL';
		formModelType = 'AIWP';
		formInitDays = '';
		formInitDaysSource = '';
		validationDraft = null;
		validatedSignature = '';
	}

	function onKindChange() {
		formVar = formKind === 'obs' ? 'RAINFALL' : 'tp';
		formInitDays = '';
		formInitDaysSource = '';
		validationDraft = null;
	}

	function onEdit(source: DataSource) {
		editingId = source.id;
		formKind = source.kind;
		formName = source.name;
		formPath = source.path;
		formRegion = source.region ?? '';
		formFilePattern = String(
			source.metadata[source.kind === 'obs' ? 'obs_file_pattern' : 'file_pattern'] ?? '{}.nc'
		);
		formVar = String(
			source.metadata[source.kind === 'obs' ? 'obs_var' : 'model_var'] ??
				(source.kind === 'obs' ? 'RAINFALL' : 'tp')
		);
		formModelType = String(source.metadata.model_type ?? 'AIWP');
		formInitDays = String(source.metadata.init_days ?? '');
		formInitDaysSource = String(source.metadata.init_days_source ?? '');
		validationDraft = null;
		window.scrollTo({ top: 0, behavior: 'smooth' });
	}

	function toggleInitializationDay(day: number, checked: boolean) {
		const days = new Set(
			formInitDays
				.split(',')
				.map((value) => Number(value.trim()))
				.filter((value) => Number.isInteger(value) && value >= 0 && value <= 6)
		);
		if (checked) days.add(day);
		else days.delete(day);
		formInitDays = [...days].sort((left, right) => left - right).join(',');
		formInitDaysSource = 'configured';
	}

	async function onRevalidate(source: DataSource) {
		revalidatingId = source.id;
		error = null;
		try {
			const updated = await revalidateDataSource(source.id);
			sources = sources.map((item) => (item.id === updated.id ? updated : item));
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			revalidatingId = null;
		}
	}

	async function onDelete(source: DataSource) {
		if (
			!confirm(`Remove "${source.name}" from the catalog? (The files on disk are not deleted.)`)
		) {
			return;
		}
		try {
			await deleteDataSource(source.id);
			sources = sources.filter((s) => s.id !== source.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	const obsSources = $derived(sources.filter((s) => s.kind === 'obs'));
	const modelSources = $derived(sources.filter((s) => s.kind === 'model'));
	const selectedRegion = $derived(regions.find((region) => region.id === formRegion) ?? null);

	function regionName(regionId: string | null): string | null {
		if (!regionId) return null;
		return regions.find((region) => region.id === regionId)?.display_name ?? regionId;
	}

	function spatialBoundsMetadata(metadata: Record<string, unknown>): string | null {
		const bounds = metadata.spatial_bounds;
		if (!bounds || typeof bounds !== 'object') return null;
		const values = bounds as Record<string, unknown>;
		const latMin = Number(values.lat_min);
		const latMax = Number(values.lat_max);
		const lonMin = Number(values.lon_min);
		const lonMax = Number(values.lon_max);
		if (![latMin, latMax, lonMin, lonMax].every(Number.isFinite)) return null;
		return `Latitude ${latMin}° to ${latMax}° · Longitude ${lonMin}° to ${lonMax}°`;
	}

	function spatialBounds(source: DataSource): string | null {
		return spatialBoundsMetadata(source.metadata);
	}

	const weekdayNames = [
		'Monday',
		'Tuesday',
		'Wednesday',
		'Thursday',
		'Friday',
		'Saturday',
		'Sunday'
	];

	function initializationSchedule(source: DataSource): string | null {
		if (source.kind !== 'model') return null;
		const initDays = String(source.metadata.init_days ?? '').trim();
		if (!initDays) return null;
		const days = initDays.split(',').map((value) => Number(value.trim()));
		if (days.some((day) => !Number.isInteger(day) || day < 0 || day > 6)) {
			return `Initialization days: ${initDays}`;
		}
		const sourceLabel =
			source.metadata.init_days_source === 'inferred'
				? 'inferred from NetCDF'
				: source.metadata.init_days_source === 'default'
					? 'default'
					: 'configured';
		return `Initializes ${days.map((day) => weekdayNames[day]).join(' and ')} · ${sourceLabel}`;
	}

	function initializationDaySelected(day: number): boolean {
		return formInitDays.split(',').some((value) => Number(value.trim()) === day);
	}
</script>

<svelte:head>
	<title>Data Sources · AI Almanac</title>
</svelte:head>

<main class="wrap">
	<DataCatalogNav />

	<DataCatalogPageHeader
		eyebrow="Catalog entries"
		title="Data sources"
		description="Register the local directories that hold your observation datasets and model forecast outputs. Each entry is a pointer — the app reads the files but never modifies them."
	/>

	{#if error}
		<div class="banner err">{error}</div>
	{/if}

	{#if account.isAdmin}
	<section class="add">
		<h2>{editingId ? 'Edit source' : 'Add a source'}</h2>
		<form onsubmit={onSubmit}>
			<div class="row">
				<label>
					<span>Kind</span>
					<select bind:value={formKind} disabled={editingId !== null} onchange={onKindChange}>
						<option value="obs">Observations</option>
						<option value="model">Model forecasts</option>
					</select>
				</label>
				<label class="grow">
					<span>Name</span>
					<input
						type="text"
						bind:value={formName}
						placeholder={formKind === 'obs' ? 'e.g. ERA5 Ethiopia' : 'e.g. FuXi Ethiopia 2018-2022'}
						required
					/>
				</label>
			</div>
			<div class="path-row">
				<label class="grow">
					<span>Directory</span>
					<input
						type="text"
						bind:value={formPath}
						placeholder="/data/era5/ethiopia or /home/me/forecasts/fuxi"
						required
					/>
				</label>
				<button type="button" class="browse" onclick={() => (pickerOpen = true)}>Browse</button>
			</div>
			<div class="row">
				<label class="grow">
					<span>Region <em>(required)</em></span>
					<select bind:value={formRegion} required>
						<option value="" disabled>Choose the dataset coverage</option>
						{#if formRegion && !regions.some((region) => region.id === formRegion)}
							<option value={formRegion}>{formRegion} (no longer configured)</option>
						{/if}
						{#each regions as region}
							<option value={region.id}>{region.display_name}</option>
						{/each}
					</select>
					{#if selectedRegion}
						<small>
							{selectedRegion.romp_region === 'custom'
								? selectedRegion.id === 'custom'
									? 'Geographic bounds will be inferred from the NetCDF coordinates during validation.'
									: 'Benchmarks use this region’s configured geographic bounds.'
								: `Benchmarks use ROMP’s ${selectedRegion.romp_region} region definition.`}
						</small>
					{/if}
				</label>
				<label class="grow">
					<span>File pattern</span>
					<input type="text" bind:value={formFilePattern} placeholder={`{}.nc`} />
				</label>
			</div>
			<div class="row">
				<label class="grow">
					<span>NetCDF variable</span>
					<input type="text" bind:value={formVar} placeholder="tp" required />
				</label>
				{#if formKind === 'model'}
					<label class="grow">
						<span>Model type</span>
						<select bind:value={formModelType}>
							<option value="AIWP">AIWP (AI weather prediction)</option>
							<option value="NWP">NWP (numerical)</option>
							<option value="climatology">Climatology</option>
						</select>
					</label>
					{#if editingId}
						<label class="grow">
							<span>Initialization days</span>
							<input
								type="text"
								bind:value={formInitDays}
								oninput={() => (formInitDaysSource = 'configured')}
								placeholder="e.g. 0,3 for Monday and Thursday"
								required
							/>
							<small>Use weekday numbers from 0 (Monday) through 6 (Sunday).</small>
						</label>
					{/if}
				{/if}
			</div>
			{#if !editingId && validationDraft && validationIsCurrent}
				<div class:invalid={validationDraft.status === 'invalid'} class="validation-review">
					<div class="review-heading">
						<div>
							<span class="review-kicker">
								{validationDraft.status === 'ready' ? 'Validation complete' : 'Validation failed'}
							</span>
							<strong>
								{validationDraft.status === 'ready'
									? 'Review before adding'
									: 'Update the source details and try again'}
							</strong>
						</div>
						<span class:warn={validationDraft.status === 'invalid'} class="tag">
							{validationDraft.status}
						</span>
					</div>

					{#if validationDraft.status === 'ready'}
						<div class="review-facts">
							<div>
								<span>Files</span>
								<strong>{formFilePattern}</strong>
							</div>
							<div>
								<span>Variable</span>
								<strong>{formVar}</strong>
							</div>
							{#if validationDraft.metadata.start_year && validationDraft.metadata.end_year}
								<div>
									<span>Detected years</span>
									<strong>
										{String(validationDraft.metadata.start_year)}–{String(
											validationDraft.metadata.end_year
										)}
									</strong>
								</div>
							{/if}
							{#if spatialBoundsMetadata(validationDraft.metadata)}
								<div class="wide">
									<span>Detected coverage</span>
									<strong>{spatialBoundsMetadata(validationDraft.metadata)}</strong>
								</div>
							{/if}
						</div>

						{#if formKind === 'model'}
							<fieldset class="weekday-fieldset">
								<legend>Forecast initialization days</legend>
								<p>
									Confirm the days this model initializes. Benchmarks will use this schedule by
									default.
								</p>
								<div class="weekday-options">
									{#each weekdayNames as weekday, day}
										<label class="weekday-option">
											<input
												type="checkbox"
												checked={initializationDaySelected(day)}
												onchange={(event) =>
													toggleInitializationDay(day, (event.target as HTMLInputElement).checked)}
											/>
											<span>{weekday.slice(0, 3)}</span>
										</label>
									{/each}
								</div>
								<small>
									{formInitDaysSource === 'inferred'
										? 'Inferred from the NetCDF initialization-time coordinate.'
										: 'Using your configured schedule.'}
								</small>
							</fieldset>
						{/if}
					{:else}
						<p class="validation-error">{validationDraft.validation_error}</p>
					{/if}
				</div>
			{/if}
			{#if !editingId && validationDraft && !validationIsCurrent}
				<p class="draft-stale">Source details changed. Validate again to refresh the review.</p>
			{/if}
			<div class="actions">
				{#if editingId}
					<button type="button" class="secondary" onclick={resetForm}>Cancel</button>
				{/if}
				{#if !editingId && validationDraft?.status === 'ready' && validationIsCurrent}
					<button type="button" class="secondary" onclick={validateDraft} disabled={submitting}>
						Validate again
					</button>
					<button
						type="button"
						onclick={confirmAdd}
						disabled={submitting || (formKind === 'model' && !formInitDays)}
					>
						{submitting ? 'Adding…' : 'Add data source'}
					</button>
				{:else}
					<button
						type="submit"
						disabled={submitting || !formName.trim() || !formPath.trim() || !formRegion}
					>
						{submitting
							? editingId
								? 'Saving…'
								: 'Validating…'
							: editingId
								? 'Save and validate'
								: 'Validate directory'}
					</button>
				{/if}
			</div>
		</form>
	</section>
	{/if}

	<section>
		<h2>Observation datasets <span class="count">({obsSources.length})</span></h2>
		{#if loading}
			<p class="muted">Loading…</p>
		{:else if obsSources.length === 0}
			<p class="muted">
				No obs datasets registered. Add one above to make it available to the benchmark UI.
			</p>
		{:else}
			<ul class="sources">
				{#each obsSources as src (src.id)}
					{@render sourceRow(src)}
				{/each}
			</ul>
		{/if}
	</section>

	<section>
		<h2>Model forecast directories <span class="count">({modelSources.length})</span></h2>
		{#if loading}
			<p class="muted">Loading…</p>
		{:else if modelSources.length === 0}
			<p class="muted">
				No model directories registered. Add one above to enable it in benchmark submissions.
			</p>
		{:else}
			<ul class="sources">
				{#each modelSources as src (src.id)}
					{@render sourceRow(src)}
				{/each}
			</ul>
		{/if}
	</section>
</main>

<FilePicker
	bind:open={pickerOpen}
	mode="directory"
	initialPath={formPath}
	title="Choose data source directory"
	onclose={() => (pickerOpen = false)}
	onselect={(p) => (formPath = p)}
/>

{#snippet sourceRow(src: DataSource)}
	<li class="source" class:missing={src.status === 'invalid'}>
		<div class="meta">
			<div class="name">
				{src.name}
				{#if regionName(src.region)}<span class="tag">{regionName(src.region)}</span>{/if}
				<span class:warn={src.status === 'invalid'} class="tag">{src.status}</span>
			</div>
			<code class="path">{src.path}</code>
			{#if spatialBounds(src)}
				<p class="coverage">{spatialBounds(src)}</p>
			{/if}
			{#if initializationSchedule(src)}
				<p class="coverage">{initializationSchedule(src)}</p>
			{/if}
			{#if src.validation_error}
				<p class="validation-error">{src.validation_error}</p>
			{/if}
		</div>
		{#if account.isAdmin}
			<div class="source-actions">
				<button class="rm" onclick={() => onEdit(src)}>Edit</button>
				<button class="rm" disabled={revalidatingId === src.id} onclick={() => onRevalidate(src)}>
					{revalidatingId === src.id ? 'Checking…' : 'Revalidate'}
				</button>
				<button class="rm" onclick={() => onDelete(src)} aria-label="Remove">Remove</button>
			</div>
		{/if}
	</li>
{/snippet}

<style>
	.wrap {
		width: min(100% - 2rem, 68rem);
		margin: 2.75rem auto 5rem;
		display: flex;
		flex-direction: column;
		gap: 2.75rem;
	}
	.banner.err {
		padding: 0.75rem 1rem;
		border-radius: 0.5rem;
		background: color-mix(in oklab, var(--color-danger, #c33) 12%, transparent);
		color: var(--color-danger, #c33);
		border: 1px solid color-mix(in oklab, var(--color-danger, #c33) 30%, transparent);
	}
	section {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	section h2 {
		margin: 0;
		font-size: 1.15rem;
	}
	.count {
		color: var(--color-text-muted);
		font-weight: 400;
		font-size: 0.95rem;
	}
	.add form {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 1.25rem;
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
	}
	.row {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
	}
	.path-row {
		display: flex;
		gap: 0.6rem;
		align-items: flex-end;
		width: 100%;
	}
	.browse {
		background: transparent;
		color: var(--color-text);
		border-color: var(--color-border);
		font-weight: 500;
		padding: 0.5rem 0.85rem;
		flex-shrink: 0;
		align-self: flex-end;
		margin-bottom: 0;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		font-size: 0.9rem;
	}
	label.grow {
		flex: 1;
		min-width: 12rem;
	}
	label > span {
		color: var(--color-text-muted);
	}
	label small {
		color: var(--color-text-muted);
		line-height: 1.35;
	}
	label em {
		color: var(--color-danger, #c33);
		font-style: normal;
		font-size: 0.8rem;
	}
	input,
	select {
		padding: 0.5rem 0.65rem;
		border-radius: 0.45rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.65rem;
	}
	button {
		padding: 0.55rem 1.1rem;
		border-radius: 0.45rem;
		border: 1px solid var(--color-border);
		background: var(--color-text);
		color: var(--color-bg);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.sources {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.source {
		display: flex;
		gap: 1rem;
		align-items: center;
		justify-content: space-between;
		padding: 0.8rem 1rem;
		border: 1px solid var(--color-border);
		border-radius: 0.55rem;
		background: var(--color-surface-raised);
	}
	.source.missing {
		border-color: color-mix(in oklab, var(--color-danger, #c33) 35%, var(--color-border));
		background: color-mix(in oklab, var(--color-danger, #c33) 4%, var(--color-surface-raised));
	}
	.meta {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		min-width: 0;
		flex: 1;
	}
	.name {
		font-weight: 600;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.tag {
		font-size: 0.75rem;
		padding: 0.15rem 0.5rem;
		border-radius: 999px;
		background: var(--color-surface);
		color: var(--color-text-muted);
		font-weight: 400;
	}
	.tag.warn {
		background: color-mix(in oklab, var(--color-danger, #c33) 18%, transparent);
		color: var(--color-danger, #c33);
	}
	.path {
		font-size: 0.85rem;
		color: var(--color-text-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.rm {
		background: transparent;
		color: var(--color-text-muted);
		border-color: var(--color-border);
		font-weight: 500;
	}
	.secondary {
		background: transparent;
		color: var(--color-text);
	}
	.source-actions {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
		justify-content: flex-end;
	}
	.validation-error {
		margin: 0;
		color: var(--color-danger, #c33);
		font-size: 0.82rem;
	}
	.validation-review {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 1rem;
		border: 1px solid color-mix(in oklab, var(--color-accent) 35%, var(--color-border));
		border-radius: 0.55rem;
		background: color-mix(in oklab, var(--color-accent) 4%, var(--color-surface));
	}
	.validation-review.invalid {
		border-color: color-mix(in oklab, var(--color-danger, #c33) 35%, var(--color-border));
		background: color-mix(in oklab, var(--color-danger, #c33) 4%, var(--color-surface));
	}
	.review-heading {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}
	.review-heading > div {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}
	.review-kicker {
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}
	.review-facts {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem 2rem;
	}
	.review-facts > div {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		min-width: 10rem;
	}
	.review-facts .wide {
		flex: 1 1 100%;
	}
	.review-facts span {
		color: var(--color-text-muted);
		font-size: 0.78rem;
	}
	.review-facts strong {
		font-size: 0.9rem;
		font-weight: 600;
	}
	.weekday-fieldset {
		display: flex;
		flex-direction: column;
		gap: 0.7rem;
		margin: 0;
		padding: 0.9rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
	}
	.weekday-fieldset legend {
		padding: 0 0.35rem;
		font-size: 0.9rem;
		font-weight: 600;
	}
	.weekday-fieldset p,
	.weekday-fieldset small {
		margin: 0;
		color: var(--color-text-muted);
		line-height: 1.4;
	}
	.weekday-options {
		display: flex;
		flex-wrap: wrap;
		gap: 0.45rem;
	}
	.weekday-option {
		display: flex;
		flex-direction: row;
		align-items: center;
		gap: 0.35rem;
		padding: 0.4rem 0.55rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface-raised);
		cursor: pointer;
	}
	.weekday-option:has(input:checked) {
		border-color: var(--color-accent);
		background: color-mix(in oklab, var(--color-accent) 9%, var(--color-surface-raised));
	}
	.weekday-option input {
		margin: 0;
	}
	.draft-stale {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}
	.coverage {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.82rem;
	}
	.muted {
		color: var(--color-text-muted);
	}
</style>
