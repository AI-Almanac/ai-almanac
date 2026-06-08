<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listDataSources,
		createDataSource,
		deleteDataSource,
		type DataSource
	} from '$lib/api';

	let sources = $state<DataSource[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let formKind = $state<'obs' | 'model'>('obs');
	let formName = $state('');
	let formPath = $state('');
	let formRegion = $state('');
	let formFilePattern = $state('{}.nc');
	let formVar = $state('tp');
	let formModelType = $state('AIWP');
	let submitting = $state(false);

	async function load() {
		loading = true;
		error = null;
		try {
			sources = await listDataSources();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);

	async function onSubmit(event: Event) {
		event.preventDefault();
		submitting = true;
		error = null;
		try {
			const metadata: Record<string, unknown> = {};
			if (formKind === 'obs') {
				if (formFilePattern) metadata.obs_file_pattern = formFilePattern;
			} else {
				if (formVar) metadata.model_var = formVar;
				if (formModelType) metadata.model_type = formModelType;
				if (formFilePattern) metadata.file_pattern = formFilePattern;
			}
			await createDataSource({
				kind: formKind,
				name: formName.trim(),
				path: formPath.trim(),
				region: formKind === 'model' ? formRegion.trim() || undefined : formRegion.trim() || undefined,
				metadata
			});
			formName = '';
			formPath = '';
			await load();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			submitting = false;
		}
	}

	async function onDelete(source: DataSource) {
		if (!confirm(`Remove "${source.name}" from the catalog? (The files on disk are not deleted.)`)) {
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
</script>

<svelte:head>
	<title>Data Sources · AI Almanac</title>
</svelte:head>

<main class="wrap">
	<header>
		<h1>Data Sources</h1>
		<p class="lede">
			Register the local directories that hold your observation datasets and model forecast
			outputs. Each entry is a pointer — the app reads the files but never modifies them.
		</p>
	</header>

	{#if error}
		<div class="banner err">{error}</div>
	{/if}

	<section class="add">
		<h2>Add a source</h2>
		<form onsubmit={onSubmit}>
			<div class="row">
				<label>
					<span>Kind</span>
					<select bind:value={formKind}>
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
			<label class="full">
				<span>Path on disk</span>
				<input
					type="text"
					bind:value={formPath}
					placeholder="/data/era5/ethiopia or /home/me/forecasts/fuxi"
					required
				/>
			</label>
			<div class="row">
				<label class="grow">
					<span>Region {#if formKind === 'model'}<em>(required)</em>{/if}</span>
					<input
						type="text"
						bind:value={formRegion}
						placeholder="ethiopia, india, bangladesh, …"
						required={formKind === 'model'}
					/>
				</label>
				<label class="grow">
					<span>File pattern</span>
					<input type="text" bind:value={formFilePattern} placeholder="{`{}.nc`}" />
				</label>
			</div>
			{#if formKind === 'model'}
				<div class="row">
					<label class="grow">
						<span>Variable</span>
						<input type="text" bind:value={formVar} placeholder="tp" />
					</label>
					<label class="grow">
						<span>Model type</span>
						<select bind:value={formModelType}>
							<option value="AIWP">AIWP (AI weather prediction)</option>
							<option value="NWP">NWP (numerical)</option>
							<option value="climatology">Climatology</option>
						</select>
					</label>
				</div>
			{/if}
			<div class="actions">
				<button type="submit" disabled={submitting || !formName.trim() || !formPath.trim()}>
					{submitting ? 'Adding…' : 'Add to catalog'}
				</button>
			</div>
		</form>
	</section>

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

{#snippet sourceRow(src: DataSource)}
	<li class="source" class:missing={!src.exists}>
		<div class="meta">
			<div class="name">
				{src.name}
				{#if src.region}<span class="tag">{src.region}</span>{/if}
				{#if !src.exists}<span class="tag warn">path missing</span>{/if}
			</div>
			<code class="path">{src.path}</code>
		</div>
		<button class="rm" onclick={() => onDelete(src)} aria-label="Remove">Remove</button>
	</li>
{/snippet}

<style>
	.wrap {
		width: min(100% - 2rem, 60rem);
		margin: 2.5rem auto 4rem;
		display: flex;
		flex-direction: column;
		gap: 2.25rem;
	}
	header h1 {
		margin: 0 0 0.4rem;
	}
	.lede {
		margin: 0;
		color: var(--color-text-muted);
		max-width: 50rem;
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
	label.full {
		width: 100%;
	}
	label > span {
		color: var(--color-text-muted);
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
	.muted {
		color: var(--color-text-muted);
	}
</style>
