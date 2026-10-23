<script lang="ts">
	import {
		createDataSource,
		discoverDataSources,
		type DataSourceDiscovery,
		type ForecastModel,
		type Region
	} from '$lib/api';
	import FilePicker from '$lib/FilePicker.svelte';
	import {
		canRegister,
		initialChoices,
		initialSelection,
		proposalSummary,
		registrationBody,
		type DiscoveryChoice
	} from './discovery';
	import { linkableModels } from './live-links';

	let {
		regions,
		forecastModels,
		canBrowse,
		startOpen,
		onregistered
	}: {
		regions: Region[];
		forecastModels: ForecastModel[];
		canBrowse: boolean;
		// Open until the user toggles or uses the panel; the page opens it while
		// nothing is registered.
		startOpen: boolean;
		onregistered: () => void | Promise<void>;
	} = $props();

	let open = $state(false);
	let touched = false;
	$effect(() => {
		if (!touched) open = startOpen;
	});

	let root = $state('');
	let pickerOpen = $state(false);
	let scanning = $state(false);
	let registering = $state(false);
	let error = $state<string | null>(null);
	let discovery = $state<DataSourceDiscovery | null>(null);
	let checked = $state<Record<string, boolean>>({});
	let choices = $state<Record<string, DiscoveryChoice>>({});
	// Registration failures by path; successes leave the list instead.
	let failures = $state<Record<string, string>>({});
	let registeredNow = $state(0);

	// Registered sources leave the list; it shows only what's left to decide on.
	const pending = $derived((discovery?.sources ?? []).filter((source) => !source.registered_as));
	const alreadyRegistered = $derived(
		(discovery?.sources.length ?? 0) - pending.length - registeredNow
	);
	const selectedCount = $derived(
		pending.filter(
			(source) => checked[source.path] && canRegister(source) && choices[source.path]?.region
		).length
	);

	async function scan(event?: Event) {
		event?.preventDefault();
		touched = true;
		scanning = true;
		error = null;
		failures = {};
		registeredNow = 0;
		try {
			const result = await discoverDataSources(root.trim() || undefined);
			checked = initialSelection(result.sources);
			choices = initialChoices(result.sources);
			discovery = result;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			scanning = false;
		}
	}

	async function registerSelected() {
		if (!discovery) return;
		touched = true;
		registering = true;
		error = null;
		let registered = 0;
		for (const source of pending) {
			const choice = choices[source.path];
			if (!checked[source.path] || !canRegister(source) || !choice?.region) continue;
			try {
				const created = await createDataSource(registrationBody(source, choice));
				source.registered_as = created.name;
				delete failures[source.path];
				registered += 1;
			} catch (e) {
				failures[source.path] = e instanceof Error ? e.message : String(e);
			}
		}
		registeredNow += registered;
		registering = false;
		if (registered) await onregistered();
	}
</script>

<details class="discover" bind:open>
	<summary onclick={() => (touched = true)}>
		<span class="title">Find sources</span>
		<span class="hint">Scan a folder and register what's in it in one go</span>
	</summary>

	<div class="body">
		<p class="muted">
			Each directory of yearly NetCDF files found is checked the same way as adding it by hand, and
			nothing is registered until you choose.
		</p>

		<form class="scan-row" onsubmit={scan}>
			<label class="grow">
				<span>Folder to scan</span>
				<input type="text" bind:value={root} placeholder="The dataset mount roots" />
			</label>
			{#if canBrowse}
				<button type="button" class="secondary" onclick={() => (pickerOpen = true)}>Browse</button>
			{/if}
			<button type="submit" disabled={scanning || registering}>
				{scanning ? 'Scanning…' : 'Scan'}
			</button>
		</form>

		{#if error}
			<p class="validation-error">{error}</p>
		{/if}

		{#if discovery}
			<p class="muted">
				{#if registeredNow}
					<strong class="ok">Registered {registeredNow}.</strong>
				{/if}
				{pending.length
					? `${pending.length} to register in ${discovery.roots.join(', ')}.`
					: registeredNow || alreadyRegistered
						? `Nothing left to register in ${discovery.roots.join(', ')}.`
						: `No data sources found in ${discovery.roots.join(', ')}.`}
				{#if alreadyRegistered > 0}
					{alreadyRegistered} already registered.
				{/if}
				{#if discovery.truncated}
					The scan stopped early in a large folder; scan a narrower one to see the rest.
				{/if}
			</p>

			{#if pending.length}
				<ul class="candidates">
					{#each pending as source (source.path)}
						{@const registrable = canRegister(source)}
						{@const choice = choices[source.path]}
						<li class:invalid={source.status === 'invalid'}>
							<input
								type="checkbox"
								aria-label={`Register ${source.name}`}
								bind:checked={checked[source.path]}
								disabled={!registrable || !choice.region || registering}
							/>
							<div class="meta">
								<div class="name-row">
									<input
										class="name"
										type="text"
										aria-label="Name"
										bind:value={choice.name}
										disabled={!registrable || registering}
									/>
									<span class="tag">{source.kind === 'obs' ? 'observations' : 'model'}</span>
									{#if source.status === 'invalid'}
										<span class="tag warn">invalid</span>
									{/if}
								</div>
								<code class="path">{source.path}</code>
								<p class="coverage">{proposalSummary(source)}</p>
								{#if source.validation_error}
									<p class="validation-error">{source.validation_error}</p>
								{/if}
								{#if failures[source.path]}
									<p class="validation-error">{failures[source.path]}</p>
								{/if}
							</div>
							<div class="choices">
								<select
									aria-label="Region"
									bind:value={choice.region}
									disabled={!registrable || registering}
									onchange={() => (checked[source.path] = true)}
								>
									<option value="" disabled>Choose a region</option>
									{#each regions as region (region.id)}
										<option value={region.id}>{region.display_name}</option>
									{/each}
								</select>
								{#if source.kind === 'model'}
									<select
										aria-label="Live forecast model"
										bind:value={choice.forecastModelId}
										disabled={!registrable || registering}
									>
										<option value="">No live forecasts</option>
										{#each linkableModels(forecastModels, source.metadata.grid_step_deg, choice.forecastModelId) as model (model.id)}
											<option value={model.id}>Live: {model.display_name}</option>
										{/each}
									</select>
								{/if}
							</div>
						</li>
					{/each}
				</ul>
			{/if}

			{#if discovery.skipped.length}
				<details class="skipped">
					<summary>
						{discovery.skipped.length}
						{discovery.skipped.length === 1 ? 'folder' : 'folders'} with NetCDF files skipped
					</summary>
					<ul>
						{#each discovery.skipped as item (item.path)}
							<li><code>{item.path}</code> — {item.reason}</li>
						{/each}
					</ul>
				</details>
			{/if}

			{#if pending.some(canRegister)}
				<div class="actions">
					<button type="button" onclick={registerSelected} disabled={!selectedCount || registering}>
						{registering ? 'Registering…' : `Register ${selectedCount} selected`}
					</button>
				</div>
			{/if}
		{/if}
	</div>
</details>

<FilePicker
	bind:open={pickerOpen}
	mode="directory"
	initialPath={root}
	title="Choose a folder to scan"
	onclose={() => (pickerOpen = false)}
	onselect={(path) => (root = path)}
/>

<style>
	.discover {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
	}
	.discover > summary {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		flex-wrap: wrap;
		padding: 1rem 1.25rem;
		cursor: pointer;
	}
	.discover > summary .title {
		font-size: 1.15rem;
		font-weight: 650;
	}
	.discover > summary .hint {
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.body {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 0 1.25rem 1.25rem;
	}
	.choices {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		min-width: 13rem;
	}
	.ok {
		color: var(--color-text);
	}
	.muted {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.9rem;
		line-height: 1.4;
	}
	.scan-row {
		display: flex;
		gap: 0.6rem;
		align-items: flex-end;
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
	input[type='text'],
	select {
		padding: 0.5rem 0.65rem;
		border-radius: 0.45rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
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
	.secondary {
		background: transparent;
		color: var(--color-text);
		font-weight: 500;
	}
	.candidates {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.candidates li {
		display: flex;
		gap: 0.85rem;
		align-items: center;
		padding: 0.75rem 1rem;
		border: 1px solid var(--color-border);
		border-radius: 0.55rem;
		background: var(--color-surface);
	}
	.candidates li.invalid {
		border-color: color-mix(in oklab, var(--color-danger, #c33) 35%, var(--color-border));
	}
	.meta {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		min-width: 0;
		flex: 1;
	}
	.name-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.name {
		font-weight: 600;
		min-width: 14rem;
	}
	.tag {
		font-size: 0.75rem;
		padding: 0.15rem 0.5rem;
		border-radius: 999px;
		background: var(--color-surface-raised);
		color: var(--color-text-muted);
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
	.coverage {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.82rem;
	}
	.validation-error {
		margin: 0;
		color: var(--color-danger, #c33);
		font-size: 0.82rem;
	}
	.skipped summary {
		cursor: pointer;
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.skipped ul {
		margin: 0.5rem 0 0;
		padding-left: 1.2rem;
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}
	.actions {
		display: flex;
		justify-content: flex-end;
	}
</style>
