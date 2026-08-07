<script lang="ts">
	import type { SettingsGroup } from '$lib/api';
	import { inputType, type ConfigSettingsState } from '$lib/settings/config.svelte';

	interface Props {
		settings: ConfigSettingsState;
		group: SettingsGroup;
	}

	const { settings, group }: Props = $props();

	const restartEdited = $derived(settings.restartFieldsEdited(group));
	const saving = $derived(settings.savingGroup === group.name);
</script>

<div class="group">
	<header class="grouphead">
		<h2>{group.name}</h2>
		<div class="actions">
			{#if settings.savedFlash?.startsWith(group.name)}
				<span class="flash">✓ saved</span>
			{/if}
			<button
				onclick={() => void settings.saveGroup(group)}
				disabled={saving || !settings.dirtyInGroup(group)}
			>
				{saving ? 'Saving…' : 'Save'}
			</button>
		</div>
	</header>

	{#if restartEdited.length > 0}
		<p class="banner warn">
			These changes require a server restart to fully take effect:
			<strong>{restartEdited.map((f) => f.label).join(', ')}</strong>
		</p>
	{/if}

	<div class="fields">
		{#each group.fields as field (field.name)}
			<div class="field">
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
								checked={Boolean(settings.value(field.name))}
								disabled={!field.editable}
								onchange={(e) => settings.setValue(field, e.currentTarget.checked)}
							/>
							<span>{Boolean(settings.value(field.name)) ? 'enabled' : 'disabled'}</span>
						</label>
					{:else if field.choices}
						<select
							id={`f-${field.name}`}
							value={String(settings.value(field.name) ?? field.default ?? '')}
							disabled={!field.editable}
							onchange={(e) => settings.setValue(field, e.currentTarget.value)}
						>
							{#each field.choices as choice (choice)}
								<option value={choice}>{choice}</option>
							{/each}
						</select>
					{:else if field.multiline}
						<details class="long-editor">
							<summary>Edit ({String(settings.value(field.name) ?? '').length} characters)</summary>
							<textarea
								id={`f-${field.name}`}
								rows="16"
								disabled={!field.editable}
								value={String(settings.value(field.name) ?? '')}
								oninput={(e) => settings.setValue(field, e.currentTarget.value)}></textarea>
						</details>
					{:else}
						<input
							id={`f-${field.name}`}
							type={inputType(field)}
							value={String(settings.value(field.name) ?? '')}
							disabled={!field.editable}
							oninput={(e) => settings.setValue(field, e.currentTarget.value)}
							placeholder={field.sensitive
								? settings.configured[field.name]
									? 'Enter a new key to replace'
									: 'Not set — enter a key'
								: String(field.default ?? '')}
						/>
						{#if field.sensitive && field.editable && settings.configured[field.name]}
							<button
								type="button"
								class="secondary"
								onclick={() => void settings.removeKey(field)}
								title="Remove the stored key"
							>
								Remove
							</button>
						{/if}
					{/if}
				</div>
				{#if field.sensitive}
					<p class="desc">
						{settings.configured[field.name]
							? '✓ A key is configured. Its value is never shown — to change it, enter a new key or remove it.'
							: 'No key configured.'}
					</p>
				{/if}
			</div>
		{/each}
	</div>
</div>

<style>
	.group {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.grouphead {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}
	.grouphead h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}
	.flash {
		color: var(--color-accent);
		font-size: 0.85rem;
	}
	.banner.warn {
		margin: 0;
		padding: 0.6rem 0.85rem;
		border-radius: 0.45rem;
		font-size: 0.85rem;
		color: var(--color-status-running);
		background: var(--color-status-running-bg);
		border: 1px solid var(--color-status-running);
	}
	.fields {
		display: flex;
		flex-direction: column;
		gap: 1.1rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		padding-top: 1rem;
		border-top: 1px solid var(--color-border-subtle);
	}
	.field:first-child {
		padding-top: 0;
		border-top: none;
	}
	.field label {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		font-weight: 600;
		font-size: 0.92rem;
	}
	.badge {
		font-size: 0.62rem;
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
		font-size: 0.82rem;
	}
	.control {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}
	input[type='text'],
	input[type='password'],
	input[type='number'],
	select {
		flex: 1;
		min-width: 0;
		padding: 0.45rem 0.6rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.84rem;
	}
	input:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	button {
		padding: 0.4rem 0.85rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-text);
		color: var(--color-bg);
		font: inherit;
		font-weight: 600;
		font-size: 0.82rem;
		cursor: pointer;
	}
	button.secondary {
		background: transparent;
		color: var(--color-text-muted);
	}
	button:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.long-editor {
		width: 100%;
	}
	.long-editor summary {
		cursor: pointer;
		color: var(--color-text-muted);
		font-size: 0.82rem;
	}
	.long-editor textarea {
		width: 100%;
		min-height: 18rem;
		margin-top: 0.5rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		padding: 0.6rem;
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.84rem;
		resize: vertical;
	}
	.checkrow {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		font-weight: 400;
		font-size: 0.9rem;
	}
</style>
