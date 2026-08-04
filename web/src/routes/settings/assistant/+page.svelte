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
		previewRuleset,
		compareRulesets,
		voteOnComparison,
		discardComparison,
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

	// ---- Comparison playground ------------------------------------------------

	type Column = {
		label: string;
		sessionId: string;
		text: string;
		tools: string[];
		cautions: string[];
		error: string | null;
	};

	let compareMessage = $state('');
	let arms = $state([
		{ ruleset_id: '', model: '' },
		{ ruleset_id: '', model: '' }
	]);
	let columns = $state<Column[]>([]);
	let comparisonId = $state<string | null>(null);
	let comparing = $state(false);
	let voteNote = $state('');

	function armDefaults(list: RulesetSummary[]) {
		const active = list.find((r) => r.is_active) ?? list[0];
		const other = list.find((r) => r.id !== active?.id) ?? active;
		arms[0].ruleset_id ||= active?.id ?? '';
		arms[1].ruleset_id ||= other?.id ?? '';
	}

	function applyCompareEvent(event: {
		type: string;
		variant?: number;
		[key: string]: unknown;
	}): void {
		if (event.type === 'comparison_started') {
			const variants = event.variants as {
				session_id: string;
				ruleset_name: string;
				ruleset_version: number;
				model: string | null;
			}[];
			comparisonId = event.comparison_id as string;
			columns = variants.map((variant) => ({
				label: `${variant.ruleset_name} v${variant.ruleset_version}${
					variant.model ? ` · ${variant.model}` : ''
				}`,
				sessionId: variant.session_id,
				text: '',
				tools: [],
				cautions: [],
				error: null
			}));
			return;
		}
		const column = columns[event.variant ?? -1];
		if (!column) return;
		if (event.type === 'text_delta') column.text += (event.content as string) ?? '';
		if (event.type === 'tool_call') column.tools.push((event.tool_call as { name: string }).name);
		if (event.type === 'guardrail')
			column.cautions.push(
				...((event.errors as string[]) ?? []),
				...((event.warnings as string[]) ?? [])
			);
		if (event.type === 'error') column.error = (event.message as string) ?? 'Failed';
		if (event.type === 'done') column.text = (event.turn as { content: string }).content;
	}

	async function runComparison() {
		if (!compareMessage.trim() || comparing) return;
		comparing = true;
		error = null;
		notice = null;
		// Discard the previous scratch conversations, not the ratings: the turn log
		// keeps every vote already recorded against a ruleset version.
		if (comparisonId) await discardComparison(comparisonId).catch(() => undefined);
		comparisonId = null;
		columns = [];
		voteNote = '';
		try {
			for await (const event of compareRulesets(
				compareMessage.trim(),
				arms.map((arm) => ({ ruleset_id: arm.ruleset_id, model: arm.model.trim() || null }))
			)) {
				applyCompareEvent(event as never);
			}
		} catch (e) {
			error = (e as Error).message;
		} finally {
			comparing = false;
		}
	}

	function vote(winnerSessionId: string | null) {
		const id = comparisonId;
		if (!id) return;
		void run(
			async () => {
				await voteOnComparison(id, winnerSessionId, voteNote.trim() || undefined);
			},
			winnerSessionId ? 'Vote recorded on both answers.' : 'Recorded as a tie.'
		);
	}

	function discard() {
		const id = comparisonId;
		if (!id) return;
		void run(async () => {
			await discardComparison(id);
			comparisonId = null;
			columns = [];
			voteNote = '';
		}, 'Comparison discarded. The ratings it produced are kept.');
	}

	async function load(selectId?: string) {
		loading = true;
		error = null;
		try {
			const [list, limits] = await Promise.all([listRulesets(), getGuardrailThresholds()]);
			rulesets = list;
			thresholds = limits;
			armDefaults(list);
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

<svelte:head><title>Assistant behavior · ai-almanac</title></svelte:head>

<AdminGuard>
	<div class="page">
		<header>
			<h1>Assistant behavior</h1>
			<p class="lede">
				How the assistant explains itself: which prompt sections it gets and which tools it is
				withheld. This does <strong>not</strong> control what the platform accepts — the statistical guardrails
				below are enforced server-side on every submission, whatever a ruleset says and whatever a conversation
				asks for.
			</p>
		</header>

		{#if loading}
			<p class="empty">Loading…</p>
		{:else}
			{#if error}<p class="banner error">{error}</p>{/if}
			{#if notice}<p class="banner ok">{notice}</p>{/if}

			{#if thresholds}
				<section class="card">
					<h2>Enforced thresholds</h2>
					<p class="hint">
						Read-only here. These are the numbers the submission checks apply and the numbers
						<code>&#123;&#123;placeholders&#125;&#125;</code> in a section body resolve to, so the prose
						and the check cannot disagree. Change them under Settings.
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

			<section class="card">
				<h2>Rulesets</h2>
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
								</span>
								<span class="ruleset-desc">{ruleset.description}</span>
							</button>
						</li>
					{/each}
				</ul>
			</section>

			{#if selected}
				<section class="card">
					<h2>{selected.name}</h2>
					{#if isPackaged}
						<p class="hint">
							This ruleset ships with the app and is rewritten from its YAML on every startup, so it
							cannot be saved over — an edit would look like it worked and then vanish on the next
							restart. Clone it below and edit the copy.
						</p>
					{/if}

					<div class="actions">
						<button onclick={activate} disabled={busy || selected.is_active}>
							{selected.is_active ? 'Active' : 'Make active'}
						</button>
						<button
							onclick={save}
							disabled={busy || isPackaged}
							title={isPackaged ? 'Clone this ruleset to edit it' : undefined}
						>
							Save changes
						</button>
					</div>

					<div class="clone-row">
						<input bind:value={cloneId} placeholder="new-ruleset-id" maxlength="64" />
						<input bind:value={cloneName} placeholder="Display name" maxlength="140" />
						<button onclick={clone} disabled={busy || !cloneId.trim() || !cloneName.trim()}>
							Clone to new version
						</button>
					</div>
					<p class="hint">
						Cloning rather than editing in place keeps the wording that produced the conversations
						already recorded against the current version.
					</p>

					<h3>Prompt sections</h3>
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

					<h3>Prompt preview</h3>
					<p class="hint">
						Rendered through the same code path the chat uses, so a typo'd placeholder or a section
						left off is visible before you activate.
					</p>
					<div class="preview-row">
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
				</section>
			{/if}

			<section class="card">
				<h2>Compare rulesets</h2>
				<p class="hint">
					One question, two answers, side by side. Pick two rulesets — or the same ruleset with a
					different model — and vote on which explained itself better. The vote is recorded against
					both answers' ruleset versions, so wording changes can be judged on evidence rather than
					impressions. Neither answer can submit anything: the submit tools are withheld from both.
				</p>

				<textarea
					rows="3"
					bind:value={compareMessage}
					placeholder="e.g. Which model is best for this blend?"></textarea>

				<div class="arms">
					{#each arms as arm, i (i)}
						<div class="arm">
							<span class="arm-label">{i === 0 ? 'A' : 'B'}</span>
							<select bind:value={arms[i].ruleset_id}>
								{#each rulesets as ruleset (ruleset.id)}
									<option value={ruleset.id}>{ruleset.name} (v{ruleset.version})</option>
								{/each}
							</select>
							<input bind:value={arms[i].model} placeholder="model (optional)" maxlength="200" />
						</div>
					{/each}
				</div>

				<div class="actions">
					<button onclick={runComparison} disabled={comparing || !compareMessage.trim()}>
						{comparing ? 'Running…' : 'Run comparison'}
					</button>
					{#if comparisonId && !comparing}
						<button onclick={discard} disabled={busy}>Discard</button>
					{/if}
				</div>

				{#if columns.length}
					<div class="columns">
						{#each columns as column (column.sessionId)}
							<article class="column">
								<h3 class="column-head">{column.label}</h3>
								{#if column.cautions.length}
									<ul class="cautions">
										{#each column.cautions as caution (caution)}
											<li>{caution}</li>
										{/each}
									</ul>
								{/if}
								{#if column.tools.length}
									<p class="tools">
										{#each column.tools as tool, t (t)}<span class="tag">{tool}</span>{/each}
									</p>
								{/if}
								<pre class="answer">{column.text}</pre>
								{#if column.error}<p class="banner error">{column.error}</p>{/if}
							</article>
						{/each}
					</div>

					{#if !comparing}
						<div class="vote-row">
							<input bind:value={voteNote} placeholder="Why? (optional)" maxlength="2000" />
							<button onclick={() => vote(columns[0].sessionId)} disabled={busy}>A is better</button
							>
							<button onclick={() => vote(columns[1]?.sessionId ?? null)} disabled={busy}>
								B is better
							</button>
							<button onclick={() => vote(null)} disabled={busy}>Tie</button>
						</div>
					{/if}
				{/if}
			</section>
		{/if}
	</div>
</AdminGuard>

<style>
	.page {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
		padding: 1.5rem;
		max-width: 60rem;
		margin: 0 auto;
	}
	h1 {
		margin: 0 0 0.35rem;
		font-size: 1.4rem;
	}
	h2 {
		margin: 0 0 0.5rem;
		font-size: 1rem;
	}
	h3 {
		margin: 1.25rem 0 0.5rem;
		font-size: 0.9rem;
	}
	.lede,
	.hint {
		margin: 0 0 0.5rem;
		font-size: 0.82rem;
		color: var(--color-text-muted);
		line-height: 1.5;
	}
	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-surface-raised);
		padding: 1rem;
		display: flex;
		flex-direction: column;
	}
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
	.empty {
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.thresholds {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem 1.5rem;
		margin: 0;
	}
	.thresholds div {
		display: flex;
		flex-direction: column;
	}
	.thresholds dt {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-muted);
	}
	.thresholds dd {
		margin: 0;
		font-size: 1.05rem;
		font-weight: 600;
	}
	.ruleset-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.ruleset {
		width: 100%;
		text-align: left;
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.6rem 0.7rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: transparent;
		cursor: pointer;
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
		font-size: 0.88rem;
	}
	.ruleset-desc {
		font-size: 0.78rem;
		color: var(--color-text-muted);
	}
	.tag {
		font-size: 0.62rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		padding: 0.1rem 0.35rem;
		border-radius: 3px;
		border: 1px solid var(--color-border);
		color: var(--color-text-muted);
	}
	.tag.active {
		color: var(--color-accent);
		border-color: var(--color-accent-border);
		background: var(--color-accent-light);
	}
	.tag.required {
		color: var(--color-status-running);
		border-color: var(--color-status-running);
		background: var(--color-status-running-bg);
	}
	.arms {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin: 0.5rem 0;
	}
	.arm {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		flex: 1 1 18rem;
		min-width: 0;
	}
	.arm select {
		flex: 1 1 8rem;
		min-width: 0;
	}
	.arm input {
		flex: 1 1 6rem;
		min-width: 0;
	}
	.arm-label {
		font-weight: 600;
		font-size: 0.8rem;
	}
	.columns {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		margin-top: 0.5rem;
	}
	.column {
		flex: 1 1 20rem;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		padding: 0.6rem;
		background: var(--color-surface);
	}
	.column-head {
		margin: 0;
		font-size: 0.8rem;
	}
	.cautions {
		margin: 0;
		padding-left: 1.1rem;
		font-size: 0.75rem;
		color: var(--color-status-running);
	}
	.tools {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
		margin: 0;
	}
	.answer {
		margin: 0;
		white-space: pre-wrap;
		font-family: inherit;
		font-size: 0.8rem;
		line-height: 1.55;
	}
	.vote-row {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-items: center;
		margin-top: 0.6rem;
	}
	.vote-row input {
		flex: 1 1 14rem;
		min-width: 0;
	}
	.actions,
	.clone-row,
	.preview-row {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
		margin-bottom: 0.5rem;
	}
	button {
		padding: 0.4rem 0.8rem;
		border-radius: 0.35rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		font-size: 0.82rem;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	input,
	select,
	textarea {
		padding: 0.4rem 0.5rem;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: var(--color-surface);
		font-size: 0.82rem;
		font-family: inherit;
	}
	.clone-row input {
		flex: 1 1 12rem;
		min-width: 0;
	}
	.section {
		border-top: 1px solid var(--color-border);
		padding-top: 0.6rem;
		margin-top: 0.6rem;
		display: flex;
		flex-direction: column;
	}
	.section-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin-bottom: 0.35rem;
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
		font-family: ui-monospace, monospace;
		font-size: 0.75rem;
		line-height: 1.5;
	}
	.preview {
		margin: 0;
		max-height: 24rem;
		overflow: auto;
		padding: 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: var(--color-surface);
		font-size: 0.72rem;
		line-height: 1.55;
		white-space: pre-wrap;
	}
</style>
