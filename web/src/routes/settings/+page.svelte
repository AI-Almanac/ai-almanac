<script lang="ts">
	import { getContext } from 'svelte';
	import AdminGuard from '$lib/components/AdminGuard.svelte';
	import type { ConfigSettingsState } from '$lib/settings/config.svelte';
	import { sectionSlug } from '$lib/settings/sections';

	const settings = getContext<ConfigSettingsState>('settings');
</script>

<AdminGuard>
	<section class="card">
		<h2>This deployment</h2>
		{#if settings.isShared}
			<p class="lede">
				Deployment-level settings (database, identity, storage) are managed by the environment and
				shown read-only. Everything else is editable here and takes effect immediately.
			</p>
		{:else}
			<p class="lede">
				Changes are saved to <code>{settings.configPath || 'config.yaml'}</code> and take effect immediately
				for most settings. Environment variables still override values set here.
			</p>
		{/if}
		<dl class="facts">
			<div>
				<dt>Mode</dt>
				<dd>{settings.isShared ? 'Shared host' : 'Local install'}</dd>
			</div>
			{#if !settings.isShared}
				<div>
					<dt>Config file</dt>
					<dd><code>{settings.configPath || 'config.yaml'}</code></dd>
				</div>
			{/if}
			<div>
				<dt>Sections</dt>
				<dd>{settings.groups.length}</dd>
			</div>
		</dl>
	</section>

	{#if settings.groups.length}
		<section class="card">
			<h2>Jump to a section</h2>
			<ul class="jump">
				{#each settings.groups as group (group.name)}
					<li>
						<a href={`/settings/${sectionSlug(group.name)}`}>
							<strong>{group.name}</strong>
							<small>{group.fields.length} setting{group.fields.length === 1 ? '' : 's'}</small>
						</a>
					</li>
				{/each}
			</ul>
		</section>
	{/if}
</AdminGuard>

<style>
	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
		padding: 1.1rem 1.25rem;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	.lede {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.85rem;
		line-height: 1.5;
	}
	code {
		font-size: 0.85em;
		padding: 0.1rem 0.35rem;
		border-radius: 0.25rem;
		background: var(--color-surface);
	}
	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem 2rem;
		margin: 0;
	}
	.facts div {
		display: flex;
		flex-direction: column;
	}
	.facts dt {
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-muted);
	}
	.facts dd {
		margin: 0;
		font-size: 0.95rem;
		font-weight: 600;
	}
	.jump {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.jump a {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		padding: 0.5rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		text-decoration: none;
		color: var(--color-text);
	}
	.jump a:hover {
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}
	.jump strong {
		font-size: 0.86rem;
	}
	.jump small {
		font-size: 0.7rem;
		color: var(--color-text-muted);
	}
</style>
