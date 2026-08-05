<script lang="ts">
	import { onMount } from 'svelte';
	import AdminGuard from '$lib/components/AdminGuard.svelte';
	import ChatCompare from '$lib/components/ChatCompare.svelte';
	import { ComparisonState } from '$lib/chat/compare.svelte';
	import { listRulesets, compareRulesets, type RulesetSummary } from '$lib/api';

	let rulesets = $state<RulesetSummary[]>([]);
	let error = $state<string | null>(null);
	let compareMessage = $state('');
	let arms = $state([
		{ ruleset_id: '', model: '' },
		{ ruleset_id: '', model: '' }
	]);
	const comparison = new ComparisonState();

	onMount(() => {
		listRulesets()
			.then((list) => {
				rulesets = list;
				const active = list.find((r) => r.is_active) ?? list[0];
				arms[0].ruleset_id = active?.id ?? '';
				arms[1].ruleset_id = (list.find((r) => r.id !== active?.id) ?? active)?.id ?? '';
			})
			.catch((e) => {
				error = (e as Error).message;
			});
	});

	async function runComparison() {
		if (!compareMessage.trim() || comparison.running) return;
		error = null;
		await comparison.start(
			compareMessage.trim(),
			compareRulesets(
				compareMessage.trim(),
				arms.map((arm) => ({ ruleset_id: arm.ruleset_id, model: arm.model.trim() || null }))
			)
		);
	}
</script>

<AdminGuard>
	{#if error}<p class="banner error">{error}</p>{/if}

	<section class="card">
		<h2>Compare rulesets</h2>
		<p class="hint">
			One question, two answers, side by side — with follow-ups going to both. Pick two rulesets, or
			the same ruleset on two models, and vote on which explained itself better. The vote is
			recorded against both answers' ruleset versions, so wording changes can be judged on evidence
			rather than impressions. Neither answer can submit anything: the submit tools are withheld
			from both.
		</p>
		<p class="hint">
			Users run the same comparison from the benchmark and blend chats, blinded, over whichever
			rulesets you have marked <strong>shown to users</strong>.
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
			<button onclick={runComparison} disabled={comparison.running || !compareMessage.trim()}>
				{comparison.running ? 'Running…' : 'Run comparison'}
			</button>
		</div>

		{#if comparison.arms.length || comparison.running}
			<ChatCompare {comparison} labeled onClose={() => void comparison.discard()} />
		{/if}
	</section>
</AdminGuard>

<style>
	.banner.error {
		margin: 0;
		padding: 0.6rem 0.75rem;
		border-radius: 0.45rem;
		font-size: 0.82rem;
		color: var(--color-status-failed);
		background: var(--color-status-failed-bg);
		border: 1px solid var(--color-status-failed);
	}
	.arms {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
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
	.actions {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
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
	textarea {
		width: 100%;
		resize: vertical;
	}
</style>
