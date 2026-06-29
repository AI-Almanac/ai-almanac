<script lang="ts">
	import { onMount } from 'svelte';
	import AdminGuard from '$lib/components/AdminGuard.svelte';
	import {
		getSettings,
		getSettingsSchema,
		patchSettings,
		getConfigYamlPath,
		type SettingsField,
		type SettingsGroup
	} from '$lib/api';

	let groups = $state<SettingsGroup[]>([]);
	let deploymentMode = $state<string>('personal');
	let values = $state<Record<string, unknown>>({});
	let original = $state<Record<string, unknown>>({});
	let configPath = $state<string>('');

	const isShared = $derived(deploymentMode === 'shared');
	let loading = $state(true);
	let error = $state<string | null>(null);
	let savingGroup = $state<string | null>(null);
	let savedFlash = $state<string | null>(null);
	// Whether a secret field currently has a value stored on the server. The
	// plaintext is never sent to the client; we only know if one is set.
	let configured = $state<Record<string, boolean>>({});

	async function load() {
		loading = true;
		error = null;
		try {
			const [s, v, p] = await Promise.all([
				getSettingsSchema(),
				getSettings(),
				getConfigYamlPath()
			]);
			groups = s.groups;
			deploymentMode = s.deployment_mode;
			values = { ...v.values };
			original = { ...v.values };
			// The server sends only configured/not flags for secrets — never a
			// value. Inputs start blank and only ever hold a typed replacement.
			configured = { ...v.secrets };
			for (const name of Object.keys(configured)) {
				values[name] = '';
				original[name] = '';
			}
			configPath = p;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function dirtyInGroup(group: SettingsGroup): boolean {
		return group.fields.some((f) => values[f.name] !== original[f.name]);
	}

	async function saveGroup(group: SettingsGroup) {
		savingGroup = group.name;
		error = null;
		try {
			const patch: Record<string, unknown> = {};
			for (const f of group.fields) {
				if (values[f.name] !== original[f.name]) {
					patch[f.name] = values[f.name];
				}
			}
			if (Object.keys(patch).length === 0) {
				savingGroup = null;
				return;
			}
			const updated = await patchSettings(patch);
			for (const k of Object.keys(patch)) {
				if (k in updated.secrets) {
					// A secret we just set/cleared: keep the input blank and only
					// remember whether a value is now stored.
					configured[k] = updated.secrets[k];
					values[k] = '';
					original[k] = '';
				} else {
					values[k] = updated.values[k];
					original[k] = updated.values[k];
				}
			}
			savedFlash = `${group.name} saved`;
			setTimeout(() => {
				if (savedFlash === `${group.name} saved`) savedFlash = null;
			}, 2200);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			savingGroup = null;
		}
	}

	async function removeKey(field: SettingsField) {
		error = null;
		try {
			const updated = await patchSettings({ [field.name]: '' });
			configured[field.name] = updated.secrets[field.name] ?? false;
			values[field.name] = '';
			original[field.name] = '';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	function inputType(field: SettingsField): string {
		if (field.type === 'int' || field.type === 'float') return 'number';
		if (field.sensitive) return 'password';
		return 'text';
	}

	function onValueChange(field: SettingsField, raw: string | boolean) {
		if (field.type === 'bool') {
			values[field.name] = raw;
		} else if (field.type === 'int') {
			const n = parseInt(String(raw), 10);
			values[field.name] = Number.isNaN(n) ? raw : n;
		} else if (field.type === 'float') {
			const n = parseFloat(String(raw));
			values[field.name] = Number.isNaN(n) ? raw : n;
		} else {
			values[field.name] = raw;
		}
	}

	function restartFieldsEdited(group: SettingsGroup): SettingsField[] {
		return group.fields.filter((f) => f.restart_required && values[f.name] !== original[f.name]);
	}
</script>

<svelte:head>
	<title>Settings · AI Almanac</title>
</svelte:head>

<AdminGuard>
	<main class="wrap">
		<header>
			<h1>Settings</h1>
			{#if isShared}
				<p class="lede">
					Application configuration. Deployment-level settings (database, identity, storage) are
					managed by the environment and shown read-only. The AI assistant has its own
					<a href="/settings/ai">settings page</a>.
				</p>
			{:else}
				<p class="lede">
					Application configuration. Changes are persisted to
					<code>{configPath || 'config.yaml'}</code> and take effect immediately for most settings.
					Environment variables still override values set here.
				</p>
			{/if}
		</header>

		{#if error}
			<div class="banner err">{error}</div>
		{/if}

		{#if loading}
			<p class="muted">Loading…</p>
		{:else}
			{#each groups as group (group.name)}
				<section>
					<header class="sectionhead">
						<h2>{group.name}</h2>
						<div class="actions">
							{#if savedFlash && savedFlash.startsWith(group.name)}
								<span class="flash">✓ saved</span>
							{/if}
							<button
								onclick={() => saveGroup(group)}
								disabled={savingGroup === group.name || !dirtyInGroup(group)}
							>
								{savingGroup === group.name ? 'Saving…' : 'Save'}
							</button>
						</div>
					</header>

					{#if restartFieldsEdited(group).length > 0}
						<div class="banner warn">
							These changes require a server restart to fully take effect:
							<strong
								>{restartFieldsEdited(group)
									.map((f) => f.label)
									.join(', ')}</strong
							>
						</div>
					{/if}

					<div class="fields">
						{#each group.fields as field (field.name)}
							<div class="field" class:restart={field.restart_required}>
								<label for={`f-${field.name}`}>
									<span class="fieldname">{field.label}</span>
									{#if !field.editable}<span class="badge">managed by environment</span>{/if}
									{#if field.restart_required && field.editable}<span class="badge">restart</span>{/if}
									{#if field.sensitive}<span class="badge">secret</span>{/if}
								</label>
								<p class="desc">{field.description}</p>
								<div class="control">
									{#if field.type === 'bool'}
										<label class="checkrow">
											<input
												type="checkbox"
												id={`f-${field.name}`}
												checked={Boolean(values[field.name])}
												disabled={!field.editable}
												onchange={(e) => onValueChange(field, e.currentTarget.checked)}
											/>
											<span>{Boolean(values[field.name]) ? 'enabled' : 'disabled'}</span>
										</label>
									{:else}
										<input
											id={`f-${field.name}`}
											type={inputType(field)}
											value={String(values[field.name] ?? '')}
											disabled={!field.editable}
											oninput={(e) => onValueChange(field, e.currentTarget.value)}
											placeholder={field.sensitive
												? configured[field.name]
													? 'Enter a new key to replace'
													: 'Not set — enter a key'
												: String(field.default ?? '')}
										/>
										{#if field.sensitive && field.editable && configured[field.name]}
											<button
												type="button"
												class="secretbtn"
												onclick={() => removeKey(field)}
												title="Remove the stored key"
											>
												Remove
											</button>
										{/if}
									{/if}
								</div>
								{#if field.sensitive}
									<p class="desc secretstatus">
										{configured[field.name]
											? '✓ A key is configured. Its value is never shown — to change it, enter a new key or remove it.'
											: 'No key configured.'}
									</p>
								{/if}
							</div>
						{/each}
					</div>
				</section>
			{/each}
		{/if}
	</main>
</AdminGuard>

<style>
	.wrap {
		width: min(100% - 2rem, 64rem);
		margin: 2.5rem auto 4rem;
		display: flex;
		flex-direction: column;
		gap: 2rem;
	}
	header h1 {
		margin: 0 0 0.5rem;
	}
	.lede {
		margin: 0;
		color: var(--color-text-muted);
		max-width: 50rem;
	}
	.lede code {
		font-size: 0.85em;
		padding: 0.1rem 0.4rem;
		border-radius: 0.25rem;
		background: var(--color-surface);
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
		color: #c89;
		border: 1px solid color-mix(in oklab, #c89 30%, transparent);
		font-size: 0.9rem;
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
	.sectionhead {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}
	.sectionhead h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}
	.flash {
		color: color-mix(in oklab, #5b5, 65%, var(--color-text));
		font-size: 0.85rem;
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
	}
	button:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.fields {
		display: flex;
		flex-direction: column;
		gap: 1.1rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.field label {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		font-weight: 600;
		font-size: 0.95rem;
	}
	.fieldname {
		color: var(--color-text);
	}
	.badge {
		font-size: 0.65rem;
		font-weight: 500;
		padding: 0.1rem 0.4rem;
		border-radius: 999px;
		background: var(--color-surface);
		color: var(--color-text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.desc {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}
	.control {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}
	input[type='text'],
	input[type='password'],
	input[type='number'] {
		flex: 1;
		padding: 0.5rem 0.65rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.85rem;
	}
	input:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.secretbtn {
		background: transparent;
		color: var(--color-text-muted);
		border-color: var(--color-border);
		font-weight: 500;
		padding: 0.45rem 0.75rem;
	}
	.checkrow {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		font-weight: 400;
		font-family: inherit;
		font-size: 0.95rem;
	}
	.muted {
		color: var(--color-text-muted);
	}
</style>
