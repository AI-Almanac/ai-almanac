<script lang="ts">
	import { onMount } from 'svelte';
	import { account } from '$lib/account.svelte';
	import {
		getLlmStatus,
		listLlmProfiles,
		listLlmProviders,
		createLlmProvider,
		setProviderShared,
		createLlmProfile,
		setDefaultLlmProfile,
		deleteLlmProfile,
		testLlmProfile,
		setLlmPreference,
		type LlmProvider,
		type LlmProfile,
		type LlmStatus,
		type LlmProviderType
	} from '$lib/api';

	let status = $state<LlmStatus | null>(null);
	let profiles = $state<LlmProfile[]>([]);
	let providers = $state<LlmProvider[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let busy = $state(false);

	const isAdmin = $derived(account.isAdmin);

	async function load() {
		loading = true;
		error = null;
		try {
			const [s, pr, pf] = await Promise.all([
				getLlmStatus(),
				listLlmProviders(),
				listLlmProfiles()
			]);
			status = s;
			providers = pr;
			profiles = pf;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);

	async function run(action: () => Promise<void>) {
		busy = true;
		error = null;
		try {
			await action();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}

	function choosePreference(preference: LlmStatus['preference']) {
		run(async () => {
			status = await setLlmPreference(preference);
		});
	}

	// --- BYO profiles ---
	let newProfile = $state({ provider_id: '', model_name: '', api_key: '' });

	function addProfile() {
		run(async () => {
			await createLlmProfile({
				provider_id: newProfile.provider_id,
				model_name: newProfile.model_name,
				api_key: newProfile.api_key,
				is_default: profiles.length === 0
			});
			newProfile = { provider_id: '', model_name: '', api_key: '' };
			await load();
		});
	}

	function makeDefault(id: string) {
		run(async () => {
			await setDefaultLlmProfile(id);
			await load();
		});
	}

	function removeProfile(id: string) {
		run(async () => {
			await deleteLlmProfile(id);
			await load();
		});
	}

	let testResult = $state<Record<string, string>>({});
	function test(id: string) {
		run(async () => {
			const res = await testLlmProfile(id);
			testResult[id] = res.status === 'ok' ? `ok (${res.latency_ms} ms)` : res.status;
		});
	}

	// --- Admin: providers + shared key ---
	let newProvider = $state<{
		provider_type: LlmProviderType;
		display_name: string;
		base_url: string;
	}>({ provider_type: 'openai-compatible', display_name: '', base_url: '' });

	function addProvider() {
		run(async () => {
			await createLlmProvider({
				provider_type: newProvider.provider_type,
				display_name: newProvider.display_name,
				base_url: newProvider.base_url || null
			});
			newProvider = { provider_type: 'openai-compatible', display_name: '', base_url: '' };
			await load();
		});
	}

	let sharedDraft = $state<Record<string, { shared_model_name: string; api_key: string }>>({});
	function draftFor(p: LlmProvider) {
		return (sharedDraft[p.id] ??= { shared_model_name: p.shared_model_name ?? '', api_key: '' });
	}

	function saveShared(p: LlmProvider, allow_shared: boolean) {
		const draft = draftFor(p);
		run(async () => {
			await setProviderShared(p.id, {
				allow_shared,
				shared_model_name: draft.shared_model_name || null,
				...(draft.api_key ? { api_key: draft.api_key } : {})
			});
			draft.api_key = '';
			await load();
		});
	}
</script>

<div class="wrap">
	<header>
		<h2>Model &amp; API keys</h2>
		<p class="lede">
			Choose which language model powers the assistant. Use the shared model your administrator
			provides, or bring your own API key.
		</p>
	</header>

	{#if error}<div class="banner err">{error}</div>{/if}

	{#if loading}
		<p class="muted">Loading…</p>
	{:else}
		<section>
			<h2>Which model to use</h2>
			<div class="choices">
				<label class="choice">
					<input
						type="radio"
						name="pref"
						checked={status?.preference === 'auto'}
						disabled={busy}
						onchange={() => choosePreference('auto')}
					/>
					<span>
						<strong>Automatic</strong>
						<small>Use your own key if you've added one, otherwise the shared model.</small>
					</span>
				</label>
				<label class="choice">
					<input
						type="radio"
						name="pref"
						checked={status?.preference === 'shared'}
						disabled={busy || !status?.shared_available}
						onchange={() => choosePreference('shared')}
					/>
					<span>
						<strong>Shared model</strong>
						<small>
							{status?.shared_available
								? 'Provided by your administrator.'
								: 'No shared model is available yet.'}
						</small>
					</span>
				</label>
				<label class="choice">
					<input
						type="radio"
						name="pref"
						checked={status?.preference === 'own'}
						disabled={busy}
						onchange={() => choosePreference('own')}
					/>
					<span>
						<strong>Your own key</strong>
						<small>Always use one of your personal profiles below.</small>
					</span>
				</label>
			</div>
			{#if status?.effective_source}
				<p class="muted">Currently using: <strong>{status.effective_source}</strong></p>
			{:else}
				<p class="banner warn">
					No model is available. Add your own key below
					{#if !isAdmin}or ask an administrator to enable a shared model{/if}.
				</p>
			{/if}
		</section>

		<section>
			<h2>Your API keys</h2>
			{#if providers.length === 0}
				<p class="muted">
					No providers are configured yet.
					{#if isAdmin}Add one below.{:else}Ask an administrator to add a provider.{/if}
				</p>
			{:else}
				{#if profiles.length > 0}
					<ul class="rows">
						{#each profiles as profile (profile.id)}
							<li class="row">
								<div class="row-main">
									<strong>{profile.provider_display_name}</strong>
									<span class="muted">{profile.model_name}</span>
									{#if profile.is_default}<span class="badge">default</span>{/if}
									{#if testResult[profile.id]}<span class="muted">· {testResult[profile.id]}</span
										>{/if}
								</div>
								<div class="row-actions">
									{#if !profile.is_default}
										<button disabled={busy} onclick={() => makeDefault(profile.id)}
											>Set default</button
										>
									{/if}
									<button disabled={busy} onclick={() => test(profile.id)}>Test</button>
									<button class="danger" disabled={busy} onclick={() => removeProfile(profile.id)}>
										Remove
									</button>
								</div>
							</li>
						{/each}
					</ul>
				{/if}

				<form
					class="form"
					onsubmit={(e) => {
						e.preventDefault();
						addProfile();
					}}
				>
					<h3>Add a key</h3>
					<label>
						Provider
						<select bind:value={newProfile.provider_id} required>
							<option value="" disabled>Select a provider</option>
							{#each providers as provider (provider.id)}
								<option value={provider.id}>{provider.display_name}</option>
							{/each}
						</select>
					</label>
					<label>
						Model
						<input bind:value={newProfile.model_name} placeholder="e.g. gpt-4o" required />
					</label>
					<label>
						API key
						<input type="password" bind:value={newProfile.api_key} required />
					</label>
					<button type="submit" disabled={busy}>Add key</button>
				</form>
			{/if}
		</section>

		{#if isAdmin}
			<section class="admin">
				<h2>Providers <span class="badge">admin</span></h2>
				<p class="lede">
					Providers define an LLM backend. Attach a shared key to let everyone use it without their
					own.
				</p>

				{#each providers as provider (provider.id)}
					{@const draft = draftFor(provider)}
					<div class="provider">
						<div class="row-main">
							<strong>{provider.display_name}</strong>
							<span class="muted">{provider.provider_type}</span>
							{#if provider.base_url}<span class="muted">· {provider.base_url}</span>{/if}
							{#if provider.allow_shared && provider.has_shared_key}
								<span class="badge">shared on</span>
							{/if}
						</div>
						<div class="shared-form">
							<label>
								Shared model
								<input bind:value={draft.shared_model_name} placeholder="model id" />
							</label>
							<label>
								Shared key {#if provider.has_shared_key}<span class="muted"
										>(set — leave blank to keep)</span
									>{/if}
								<input type="password" bind:value={draft.api_key} placeholder="API key" />
							</label>
							<div class="row-actions">
								<button disabled={busy} onclick={() => saveShared(provider, true)}>
									{provider.allow_shared ? 'Update shared' : 'Enable shared'}
								</button>
								{#if provider.allow_shared}
									<button
										class="danger"
										disabled={busy}
										onclick={() => saveShared(provider, false)}
									>
										Disable shared
									</button>
								{/if}
							</div>
						</div>
					</div>
				{/each}

				<form
					class="form"
					onsubmit={(e) => {
						e.preventDefault();
						addProvider();
					}}
				>
					<h3>Add a provider</h3>
					<label>
						Type
						<select bind:value={newProvider.provider_type}>
							<option value="openai-compatible">OpenAI-compatible</option>
							<option value="pydantic-ai">Pydantic AI model string</option>
						</select>
					</label>
					<label>
						Name
						<input bind:value={newProvider.display_name} placeholder="e.g. Team vLLM" required />
					</label>
					{#if newProvider.provider_type === 'openai-compatible'}
						<label>
							Base URL
							<input bind:value={newProvider.base_url} placeholder="https://host/v1" />
						</label>
					{/if}
					<button type="submit" disabled={busy}>Add provider</button>
				</form>
			</section>
		{/if}
	{/if}
</div>

<style>
	.wrap {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}
	header h2 {
		margin: 0 0 0.4rem;
		font-size: 1.15rem;
	}
	.lede {
		margin: 0;
		color: var(--color-text-muted);
		max-width: 50rem;
	}
	section {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
		padding: 1.25rem 1.5rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	section h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	.choices {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.choice {
		display: flex;
		gap: 0.75rem;
		align-items: flex-start;
	}
	.choice span {
		display: flex;
		flex-direction: column;
	}
	.choice small {
		color: var(--color-text-muted);
	}
	.rows,
	.form {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.row,
	.provider {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: center;
		gap: 0.75rem;
		padding: 0.75rem 0;
		border-top: 1px solid var(--color-border);
	}
	.provider {
		flex-direction: column;
		align-items: stretch;
	}
	.row-main {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.5rem;
	}
	.row-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.shared-form,
	.form label,
	.shared-form label {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.shared-form {
		gap: 0.75rem;
	}
	input,
	select {
		padding: 0.5rem 0.65rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
	}
	button {
		padding: 0.45rem 0.95rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-text);
		color: var(--color-bg);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
		align-self: flex-start;
	}
	button:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	button.danger {
		background: transparent;
		color: var(--color-danger, #c33);
		border-color: color-mix(in oklab, var(--color-danger, #c33) 40%, transparent);
	}
	.badge {
		font-size: 0.65rem;
		font-weight: 600;
		padding: 0.1rem 0.4rem;
		border-radius: 999px;
		background: var(--color-surface);
		color: var(--color-text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.muted {
		color: var(--color-text-muted);
	}
	.banner {
		padding: 0.75rem 1rem;
		border-radius: 0.5rem;
	}
	.banner.err {
		background: color-mix(in oklab, var(--color-danger, #c33) 12%, transparent);
		color: var(--color-danger, #c33);
		border: 1px solid color-mix(in oklab, var(--color-danger, #c33) 30%, transparent);
	}
	.banner.warn {
		background: color-mix(in oklab, #c89, 12%, transparent);
		color: #a76;
		font-size: 0.9rem;
	}
</style>
