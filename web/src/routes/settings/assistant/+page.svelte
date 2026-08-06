<script lang="ts">
	import { onMount } from 'svelte';
	import AdminGuard from '$lib/components/AdminGuard.svelte';
	import {
		listRulesets,
		getRuleset,
		getGuardrailThresholds,
		saveRuleset,
		cloneRuleset,
		activateRuleset,
		deleteRuleset,
		setRulesetComparisonEnabled,
		setRulesetAdminEnabled,
		previewRuleset,
		PREVIEW_SCOPE_KINDS,
		type RulesetSummary,
		type RulesetDetail,
		type GuardrailThresholds,
		type PromptPreview
	} from '$lib/api';

	let rulesets = $state<RulesetSummary[]>([]);
	let selected = $state<RulesetDetail | null>(null);
	let thresholds = $state<GuardrailThresholds | null>(null);
	let preview = $state<PromptPreview | null>(null);
	let previewScope = $state<string>('blend_setup');
	let loading = $state(true);
	let busy = $state(false);
	let error = $state<string | null>(null);
	let notice = $state<string | null>(null);
	let cloneId = $state('');
	let cloneName = $state('');

	const isPackaged = $derived(selected?.source === 'packaged');
	const selectedSummary = $derived(rulesets.find((r) => r.id === selected?.id));

	async function load(selectId?: string) {
		loading = true;
		error = null;
		try {
			const [list, limits] = await Promise.all([listRulesets(), getGuardrailThresholds()]);
			rulesets = list;
			thresholds = limits;
			const target = selectId ?? selected?.id ?? list.find((r) => r.is_active)?.id ?? list[0]?.id;
			if (target) await select(target);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	async function select(id: string) {
		error = null;
		preview = null;
		try {
			selected = await getRuleset(id);
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function run(action: () => Promise<void>, success: string) {
		busy = true;
		error = null;
		notice = null;
		try {
			await action();
			notice = success;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	function save() {
		const detail = selected;
		if (!detail) return;
		void run(async () => {
			// A packaged ruleset is reseeded from YAML on every startup, so the
			// backend stores an edit to one as a custom copy rather than reverting it.
			const saved = await saveRuleset(detail);
			await load(saved.id);
		}, 'Saved.');
	}

	function clone() {
		const detail = selected;
		if (!detail || !cloneId.trim() || !cloneName.trim()) return;
		void run(async () => {
			const created = await cloneRuleset(detail.id, cloneId.trim(), cloneName.trim());
			cloneId = '';
			cloneName = '';
			await load(created.id);
		}, 'Cloned to a new version.');
	}

	function activate() {
		const detail = selected;
		if (!detail) return;
		void run(async () => {
			await activateRuleset(detail.id);
			await load(detail.id);
		}, 'Active. New messages use this ruleset — no restart needed.');
	}

	function setExposure(id: string, enabled: boolean) {
		void run(
			async () => {
				await setRulesetComparisonEnabled(id, enabled);
				await load(id);
			},
			enabled
				? 'Shown to users: available in the style picker and as a comparison arm.'
				: 'Hidden from users.'
		);
	}

	function setAdminPreview(id: string, enabled: boolean) {
		void run(
			async () => {
				await setRulesetAdminEnabled(id, enabled);
				await load(id);
			},
			enabled
				? 'Visible to admins only: test it in the style picker and comparisons before users see it.'
				: 'Admin preview off.'
		);
	}

	function removeRuleset(id: string) {
		if (!confirm(`Delete the ruleset "${id}"? Its recorded feedback is kept.`)) return;
		void run(async () => {
			await deleteRuleset(id);
			selected = null;
			await load();
		}, 'Ruleset deleted. Its recorded feedback is kept.');
	}

	function showPreview() {
		const detail = selected;
		if (!detail) return;
		void run(async () => {
			preview = await previewRuleset(detail.id, previewScope);
		}, 'Preview updated.');
	}

	function scopeLabel(section: { scope_kinds: string[] }): string {
		return section.scope_kinds.length ? section.scope_kinds.join(', ') : 'all scopes';
	}

	onMount(() => {
		void load();
	});
</script>

<AdminGuard>
	{#if error}<p class="banner error">{error}</p>{/if}
	{#if notice}<p class="banner ok">{notice}</p>{/if}

	{#if loading}
		<p class="empty">Loading…</p>
	{:else}
		<section class="card">
			<h2>Rulesets</h2>
			<p class="hint">
				A ruleset is the assistant's wording: which prompt sections it gets and which tools it is
				withheld. It does <strong>not</strong> control what the platform accepts — the enforced thresholds
				below apply on every submission whatever a ruleset says.
			</p>
			<ul class="ruleset-list">
				{#each rulesets as ruleset (ruleset.id)}
					<li>
						<button
							class="ruleset"
							class:selected={selected?.id === ruleset.id}
							onclick={() => void select(ruleset.id)}
						>
							<span class="ruleset-name">
								{ruleset.name}
								<span class="tag">v{ruleset.version}</span>
								<span class="tag">{ruleset.source}</span>
								{#if ruleset.is_active}<span class="tag active">active</span>{/if}
								{#if ruleset.comparison_enabled}<span class="tag exposed">shown to users</span>{/if}
								{#if ruleset.admin_enabled && !ruleset.comparison_enabled}<span class="tag preview"
										>admins only</span
									>{/if}
							</span>
						</button>
					</li>
				{/each}
			</ul>
		</section>

		{#if selected}
			<section class="card">
				<h2>{selected.name}</h2>
				<p class="hint">{selected.description}</p>
				{#if isPackaged}
					<p class="hint">
						This ruleset ships with the app and is rewritten from its YAML on every startup, so it
						cannot be saved over — an edit would look like it worked and then vanish on the next
						restart. Clone it below and edit the copy.
					</p>
				{/if}

				<div class="actions">
					<button
						onclick={activate}
						disabled={busy || selected.is_active || selected.activatable === false}
						title={selected.activatable === false
							? 'A comparison control cannot be made active'
							: undefined}
					>
						{selected.is_active ? 'Active' : 'Make active'}
					</button>
					<button
						onclick={save}
						disabled={busy || isPackaged}
						title={isPackaged ? 'Clone this ruleset to edit it' : undefined}
					>
						Save changes
					</button>
					{#if selectedSummary?.comparison_enabled}
						<button onclick={() => setExposure(selected!.id, false)} disabled={busy}>
							Hide from users
						</button>
					{:else}
						<button
							onclick={() => setExposure(selected!.id, true)}
							disabled={busy}
							title="Users see it in the style picker and can pick it as a comparison arm; two exposed rulesets enable A/B in the chat"
						>
							Show to users
						</button>
					{/if}
					{#if selectedSummary?.admin_enabled}
						<button onclick={() => setAdminPreview(selected!.id, false)} disabled={busy}>
							Stop admin preview
						</button>
					{:else}
						<button
							onclick={() => setAdminPreview(selected!.id, true)}
							disabled={busy}
							title="Admins see it in the style picker and comparisons; users don't — test a draft before exposing it"
						>
							Preview as admin
						</button>
					{/if}
					{#if !isPackaged}
						<button
							class="danger"
							onclick={() => removeRuleset(selected!.id)}
							disabled={busy || selected.is_active}
							title={selected.is_active
								? 'Activate another ruleset first'
								: 'Removes it from every list; recorded feedback is kept'}
						>
							Delete
						</button>
					{/if}
				</div>

				<details class="drawer">
					<summary>Prompt sections ({selected.prompt_sections.length})</summary>
					{#each selected.prompt_sections as section, i (section.key)}
						<div class="section">
							<div class="section-head">
								<label class="toggle">
									<input
										type="checkbox"
										checked={section.enabled || section.required}
										disabled={section.required}
										onchange={(e) => {
											if (!selected) return;
											selected.prompt_sections[i].enabled = e.currentTarget.checked;
										}}
									/>
									<strong>{section.title || section.key}</strong>
								</label>
								<span class="section-meta">
									<span class="tag">{scopeLabel(section)}</span>
									{#if section.required}<span class="tag required">required</span>{/if}
								</span>
							</div>
							{#if section.required}
								<p class="hint">
									Required: this section cannot be turned off, so an edit elsewhere can't quietly
									drop the statistical cautions.
								</p>
							{/if}
							<textarea rows="8" bind:value={selected.prompt_sections[i].body} spellcheck="false"
							></textarea>
						</div>
					{/each}
				</details>

				<details class="drawer">
					<summary>Prompt preview</summary>
					<p class="hint">
						Rendered through the same code path the chat uses, so a typo'd placeholder or a section
						left off is visible before you activate.
					</p>
					<div class="row">
						<select bind:value={previewScope}>
							{#each PREVIEW_SCOPE_KINDS as kind}
								<option value={kind}>{kind}</option>
							{/each}
						</select>
						<button onclick={showPreview} disabled={busy}>Preview</button>
						{#if preview}<span class="hint">{preview.character_count} characters</span>{/if}
					</div>
					{#if preview}
						<pre class="preview">{preview.instructions}</pre>
					{/if}
				</details>

				<details class="drawer">
					<summary>Clone to a new version</summary>
					<p class="hint">
						Cloning rather than editing in place keeps the wording that produced the conversations
						already recorded against the current version.
					</p>
					<div class="row">
						<input bind:value={cloneId} placeholder="new-ruleset-id" maxlength="64" />
						<input bind:value={cloneName} placeholder="Display name" maxlength="140" />
						<button onclick={clone} disabled={busy || !cloneId.trim() || !cloneName.trim()}>
							Clone
						</button>
					</div>
				</details>
			</section>
		{/if}

		{#if thresholds}
			<section class="card">
				<h2>Enforced thresholds</h2>
				<p class="hint">
					Read-only here. These are the numbers the submission checks apply and the numbers
					<code>&#123;&#123;placeholders&#125;&#125;</code> in a section body resolve to, so the prose
					and the check cannot disagree. Change them under Assistant guardrails.
				</p>
				<dl class="thresholds">
					<div>
						<dt>Minimum onset years</dt>
						<dd>{thresholds.min_onset_years}</dd>
					</div>
					<div>
						<dt>Minimum training years</dt>
						<dd>{thresholds.min_training_years}</dd>
					</div>
					<div>
						<dt>Blend member warning</dt>
						<dd>{thresholds.blend_member_warn}</dd>
					</div>
					<div>
						<dt>Small-sample threshold</dt>
						<dd>{thresholds.small_sample_years}</dd>
					</div>
					<div>
						<dt>Pre-satellite era ends</dt>
						<dd>{thresholds.presatellite_end_year}</dd>
					</div>
				</dl>
			</section>
		{/if}
	{/if}
</AdminGuard>

<style>
	.banner {
		margin: 0;
		padding: 0.6rem 0.75rem;
		border-radius: 0.45rem;
		font-size: 0.82rem;
	}
	.banner.error {
		color: var(--color-status-failed);
		background: var(--color-status-failed-bg);
		border: 1px solid var(--color-status-failed);
	}
	.banner.ok {
		color: var(--color-accent);
		background: var(--color-accent-light);
		border: 1px solid var(--color-accent-border);
	}
	.ruleset-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.ruleset {
		width: 100%;
		text-align: left;
		padding: 0.45rem 0.6rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: transparent;
		cursor: pointer;
		font: inherit;
	}
	.ruleset.selected {
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}
	.ruleset-name {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		flex-wrap: wrap;
		font-size: 0.86rem;
		font-weight: 600;
	}
	.thresholds {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem 1.75rem;
		margin: 0;
	}
	.thresholds div {
		display: flex;
		flex-direction: column;
	}
	.thresholds dt {
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-muted);
	}
	.thresholds dd {
		margin: 0;
		font-size: 1rem;
		font-weight: 600;
	}
	.drawer {
		border-top: 1px solid var(--color-border-subtle);
		padding-top: 0.6rem;
	}
	.drawer summary {
		cursor: pointer;
		font-size: 0.86rem;
		font-weight: 600;
	}
	.drawer > :global(*) {
		margin-top: 0.5rem;
	}
	.actions,
	.row {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
	}
	.row input {
		flex: 1 1 10rem;
		min-width: 0;
	}
	button {
		padding: 0.4rem 0.8rem;
		border-radius: 0.35rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		font: inherit;
		font-size: 0.82rem;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	button.danger {
		color: var(--color-danger);
		border-color: var(--color-danger);
	}
	input,
	select,
	textarea {
		padding: 0.4rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: 0.82rem;
	}
	.section {
		border-top: 1px solid var(--color-border-subtle);
		padding-top: 0.6rem;
		margin-top: 0.6rem;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.section-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.85rem;
	}
	.section-meta {
		display: flex;
		gap: 0.3rem;
	}
	textarea {
		width: 100%;
		resize: vertical;
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.75rem;
		line-height: 1.5;
	}
	.preview {
		margin: 0;
		max-height: 24rem;
		overflow: auto;
		padding: 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		font-size: 0.72rem;
		line-height: 1.55;
		white-space: pre-wrap;
	}
	code {
		font-size: 0.85em;
		padding: 0.1rem 0.3rem;
		border-radius: 0.25rem;
		background: var(--color-surface);
	}
</style>
