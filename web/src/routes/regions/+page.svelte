<script lang="ts">
	import { onMount } from 'svelte';
	import {
		createRegion,
		deleteRegion,
		getRegions,
		updateRegion,
		type Region,
		type RegionWrite
	} from '$lib/api';
	import { goto } from '$app/navigation';
	import DataCatalogNav from '$lib/DataCatalogNav.svelte';
	import DataCatalogPageHeader from '$lib/DataCatalogPageHeader.svelte';
	import { account } from '$lib/account.svelte';

	$effect(() => {
		if (account.loaded && !account.canManageData) goto('/');
	});

	let regions = $state<Region[]>([]);
	let loading = $state(true);
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let editingId = $state<string | null>(null);

	let displayName = $state('');
	let description = $state('');
	let latMin = $state('');
	let latMax = $state('');
	let lonMin = $state('');
	let lonMax = $state('');
	let landOnly = $state(false);

	const customRegions = $derived(regions.filter((region) => !region.is_builtin));
	const builtInRegions = $derived(regions.filter((region) => region.is_builtin));
	const formComplete = $derived(
		displayName.trim() && [latMin, latMax, lonMin, lonMax].every((value) => value.trim() !== '')
	);

	async function load() {
		loading = true;
		error = null;
		try {
			regions = await getRegions();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function resetForm() {
		editingId = null;
		displayName = '';
		description = '';
		latMin = '';
		latMax = '';
		lonMin = '';
		lonMax = '';
		landOnly = false;
	}

	function edit(region: Region) {
		editingId = region.id;
		displayName = region.display_name;
		description = region.description;
		latMin = String(region.lat_min ?? '');
		latMax = String(region.lat_max ?? '');
		lonMin = String(region.lon_min ?? '');
		lonMax = String(region.lon_max ?? '');
		landOnly = region.land_only;
		window.scrollTo({ top: 0, behavior: 'smooth' });
	}

	function formBody(): RegionWrite {
		return {
			display_name: displayName.trim(),
			description: description.trim(),
			lat_min: Number(latMin),
			lat_max: Number(latMax),
			lon_min: Number(lonMin),
			lon_max: Number(lonMax),
			land_only: landOnly
		};
	}

	async function submit(event: Event) {
		event.preventDefault();
		submitting = true;
		error = null;
		try {
			if (editingId) {
				await updateRegion(editingId, formBody());
			} else {
				await createRegion(formBody());
			}
			resetForm();
			await load();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			submitting = false;
		}
	}

	async function remove(region: Region) {
		if (!confirm(`Remove "${region.display_name}"?`)) return;
		error = null;
		try {
			await deleteRegion(region.id);
			regions = regions.filter((item) => item.id !== region.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	function bounds(region: Region): string {
		if (
			region.lat_min === null ||
			region.lat_max === null ||
			region.lon_min === null ||
			region.lon_max === null
		) {
			return 'Bounds inferred from selected data sources';
		}
		return `${region.lat_min}° to ${region.lat_max}° latitude · ${region.lon_min}° to ${region.lon_max}° longitude`;
	}
</script>

<svelte:head>
	<title>Regions · AI Almanac</title>
</svelte:head>

<main class="wrap">
	<DataCatalogNav />

	<DataCatalogPageHeader
		eyebrow="Geographic coverage"
		title="Regions"
		description="Define geographic coverage once, then reuse it when registering datasets, adding forecast models, and running benchmarks."
	/>

	{#if error}
		<div class="banner">{error}</div>
	{/if}

	{#if account.isAdmin}
		<section class="editor">
			<h2>{editingId ? 'Edit region' : 'Create a region'}</h2>
			<form onsubmit={submit}>
				<div class="identity-fields">
					<label>
						<span>Name</span>
						<input bind:value={displayName} placeholder="e.g. Greater Horn of Africa" required />
					</label>
					<label>
						<span>Description <small>optional</small></span>
						<input
							bind:value={description}
							placeholder="Rainy season study area across eastern Africa"
						/>
					</label>
				</div>

				<div class="bounds-section">
					<div class="bounds-heading">
						<span>Geographic bounds</span>
						<small>Decimal degrees; west and south values are negative.</small>
					</div>
					<div class="bounds-grid">
						<label>
							<span>South latitude</span>
							<input type="number" bind:value={latMin} min="-90" max="90" step="any" required />
						</label>
						<label>
							<span>North latitude</span>
							<input type="number" bind:value={latMax} min="-90" max="90" step="any" required />
						</label>
						<label>
							<span>West longitude</span>
							<input type="number" bind:value={lonMin} min="-180" max="180" step="any" required />
						</label>
						<label>
							<span>East longitude</span>
							<input type="number" bind:value={lonMax} min="-180" max="180" step="any" required />
						</label>
					</div>
				</div>

				<div class="form-footer">
					<label class="check">
						<input type="checkbox" bind:checked={landOnly} />
						<span>Restrict calculations to land grid cells</span>
					</label>
					<div class="actions">
						{#if editingId}
							<button type="button" class="secondary" onclick={resetForm}>Cancel</button>
						{/if}
						<button type="submit" disabled={submitting || !formComplete}>
							{submitting ? 'Saving…' : editingId ? 'Save region' : 'Create region'}
						</button>
					</div>
				</div>
			</form>
		</section>
	{/if}

	<section>
		<div class="section-heading">
			<div>
				<p class="eyebrow">Your catalog</p>
				<h2>Reusable regions <span>({customRegions.length})</span></h2>
			</div>
		</div>
		{#if loading}
			<p class="empty">Loading regions…</p>
		{:else if customRegions.length === 0}
			<p class="empty">No reusable regions yet. Create one above to use it across your data.</p>
		{:else}
			<div class="region-list">
				{#each customRegions as region (region.id)}
					<article class="region-card">
						<div>
							<div class="title-row">
								<h3>{region.display_name}</h3>
								<span>{region.source_count} source{region.source_count === 1 ? '' : 's'}</span>
							</div>
							{#if region.description}<p>{region.description}</p>{/if}
							<code>{bounds(region)}</code>
						</div>
						{#if account.isAdmin}
							<div class="card-actions">
								<button class="secondary" onclick={() => edit(region)}>Edit</button>
								<button
									class="danger"
									disabled={region.source_count > 0}
									title={region.source_count > 0
										? 'Remove attached data sources before deleting this region'
										: 'Remove region'}
									onclick={() => remove(region)}>Remove</button
								>
							</div>
						{/if}
					</article>
				{/each}
			</div>
		{/if}
	</section>

	<section>
		<div class="section-heading">
			<div>
				<p class="eyebrow">Included</p>
				<h2>Built-in regions <span>({builtInRegions.length})</span></h2>
			</div>
			<p>Built-in definitions are maintained by AI Almanac and cannot be edited.</p>
		</div>
		<div class="built-in-grid">
			{#each builtInRegions as region (region.id)}
				<article>
					<div class="title-row">
						<h3>{region.display_name}</h3>
						<span>{region.romp_region === 'custom' ? 'Custom bounds' : 'ROMP definition'}</span>
					</div>
					<p>{region.description}</p>
					<code>{bounds(region)}</code>
				</article>
			{/each}
		</div>
	</section>
</main>

<style>
	.wrap {
		width: min(100% - 2rem, 68rem);
		margin: 2.75rem auto 5rem;
		display: flex;
		flex-direction: column;
		gap: 2.75rem;
	}
	.section-heading,
	.form-footer,
	.title-row,
	.card-actions,
	.actions {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}
	h2,
	h3,
	p {
		margin: 0;
	}
	h2 {
		font-size: 1.25rem;
	}
	h2 span {
		color: var(--color-text-muted);
		font-weight: 400;
	}
	h3 {
		font-size: 1rem;
	}
	.eyebrow {
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}
	.banner {
		padding: 0.8rem 1rem;
		border: 0.0625rem solid var(--color-danger-border);
		border-radius: 0.5rem;
		background: var(--color-danger-bg);
		color: var(--color-danger);
	}
	section {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.section-heading {
		align-items: flex-end;
	}
	.section-heading > p {
		max-width: 29rem;
		color: var(--color-text-muted);
		font-size: 0.85rem;
		text-align: right;
	}
	.editor {
		gap: 1rem;
	}
	form {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 1.25rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
	}
	.identity-fields,
	.bounds-grid,
	.built-in-grid {
		display: grid;
		gap: 1rem;
	}
	.identity-fields {
		grid-template-columns: minmax(12rem, 0.8fr) minmax(16rem, 1.5fr);
	}
	.bounds-grid {
		grid-template-columns: repeat(4, minmax(8rem, 1fr));
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		font-size: 0.9rem;
	}
	label > span {
		color: var(--color-text-muted);
	}
	label small {
		color: var(--color-text-dim);
	}
	input {
		width: 100%;
		padding: 0.5rem 0.65rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	.bounds-section {
		display: flex;
		flex-direction: column;
		gap: 0.65rem;
	}
	.bounds-heading {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.bounds-heading small {
		color: var(--color-text-dim);
	}
	.check {
		flex-direction: row;
		align-items: center;
		color: var(--color-text);
	}
	.check input {
		width: auto;
	}
	button {
		padding: 0.55rem 1.1rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.45rem;
		background: var(--color-text);
		color: var(--color-bg);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	.secondary,
	.danger {
		background: transparent;
	}
	.secondary {
		border-color: var(--color-border);
		color: var(--color-text);
	}
	.danger {
		border-color: var(--color-danger-border);
		color: var(--color-danger);
	}
	.region-list {
		display: flex;
		flex-direction: column;
		gap: 0.65rem;
	}
	.region-card,
	.built-in-grid article {
		padding: 1rem 1.1rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.6rem;
		background: var(--color-surface-raised);
	}
	.region-card {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}
	.region-card p,
	.built-in-grid p {
		margin-top: 0.25rem;
		color: var(--color-text-muted);
		font-size: 0.86rem;
	}
	.title-row {
		justify-content: flex-start;
	}
	.title-row span {
		padding: 0.12rem 0.42rem;
		border-radius: 999rem;
		background: var(--color-surface-muted);
		color: var(--color-text-muted);
		font-size: 0.7rem;
	}
	code {
		display: block;
		margin-top: 0.45rem;
		color: var(--color-text-muted);
		font-size: 0.78rem;
		white-space: normal;
	}
	.built-in-grid {
		grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
	}
	.empty {
		padding: 1.5rem;
		border: 0.0625rem dashed var(--color-border);
		border-radius: 0.6rem;
		color: var(--color-text-muted);
		text-align: center;
	}
	@media (max-width: 760px) {
		.section-heading,
		.form-footer,
		.region-card {
			align-items: stretch;
			flex-direction: column;
		}
		.section-heading > p {
			text-align: left;
		}
		.bounds-heading {
			align-items: flex-start;
			flex-direction: column;
			gap: 0.15rem;
		}
		.identity-fields,
		.bounds-grid {
			grid-template-columns: 1fr 1fr;
		}
		.actions,
		.card-actions {
			justify-content: flex-end;
		}
	}
	@media (max-width: 480px) {
		.identity-fields,
		.bounds-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
