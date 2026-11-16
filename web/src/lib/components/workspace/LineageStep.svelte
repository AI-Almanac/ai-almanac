<script lang="ts">
	import { hasEntries, type LineageRow } from '$lib/workspace/lineage';
	import CodeBlock from './CodeBlock.svelte';
	import FileText from './FileText.svelte';

	interface Props {
		workspaceId: string;
		row: LineageRow;
		onOpenTurn?: (turnId: string) => void;
	}

	let { workspaceId, row, onOpenTurn }: Props = $props();

	let open = $state<'code' | 'details' | null>(null);
	const turnId = $derived(row.steps.find((step) => step.turn_id)?.turn_id ?? null);
	const when = $derived(whenText(row));

	const DAY: Intl.DateTimeFormatOptions = { dateStyle: 'medium' };
	const TIME: Intl.DateTimeFormatOptions = { timeStyle: 'short' };

	function shortWhen(iso: string): string {
		return new Date(iso).toLocaleString([], { ...DAY, ...TIME });
	}

	/** "Nov 6, 2026, 8:58 AM to 9:28 AM": the day once when both ends share it. */
	function whenText({ steps }: LineageRow): string {
		const [first, last] = [steps[0], steps[steps.length - 1]].map((s) => new Date(s.created_at));
		if (steps.length === 1) return shortWhen(steps[0].created_at);
		const sameDay = first.toLocaleDateString([], DAY) === last.toLocaleDateString([], DAY);
		const end = sameDay ? last.toLocaleTimeString([], TIME) : shortWhen(last.toISOString());
		return `${steps.length} times, ${shortWhen(first.toISOString())} to ${end}`;
	}

	function toggle(panel: 'code' | 'details') {
		open = open === panel ? null : panel;
	}
</script>

<li class="step">
	<div class="head">
		<p class="headline">{row.headline}</p>
		{#if row.problem}<span class="badge {row.problem.tone}">{row.problem.label}</span>{/if}
	</div>
	<div class="meta">
		<span>{row.attribution}</span>
		<span>{when}</span>
		{#if row.added}<span>{row.added}</span>{/if}
		{#if turnId && onOpenTurn}
			<button type="button" class="link" onclick={() => onOpenTurn(turnId)}>Show in chat</button>
		{/if}
		{#if row.codeFileId}
			<button
				type="button"
				class="link"
				aria-expanded={open === 'code'}
				onclick={() => toggle('code')}
			>
				{open === 'code' ? 'Hide code' : 'Show code'}
			</button>
		{/if}
		<button
			type="button"
			class="link quiet"
			aria-expanded={open === 'details'}
			onclick={() => toggle('details')}
		>
			Technical details
		</button>
	</div>
	{#if open === 'code' && row.codeFileId}
		<FileText {workspaceId} fileId={row.codeFileId}>
			{#snippet children(text)}<CodeBlock code={text} capped />{/snippet}
		</FileText>
	{:else if open === 'details'}
		{#each row.steps as step (step.id)}
			<dl>
				<div>
					<dt>Operation</dt>
					<dd><code>{step.primitive} v{step.primitive_version}</code></dd>
				</div>
				{#if hasEntries(step.display_params)}
					<div>
						<dt>Settings</dt>
						<dd><pre>{JSON.stringify(step.display_params, null, 2)}</pre></dd>
					</div>
				{/if}
				{#if hasEntries(step.runtime)}
					<div>
						<dt>Runtime</dt>
						<dd><pre>{JSON.stringify(step.runtime, null, 2)}</pre></dd>
					</div>
				{/if}
				{#if step.code_hash}<div>
						<dt>Code hash</dt>
						<dd><code>{step.code_hash}</code></dd>
					</div>{/if}
				<div>
					<dt>Step hash</dt>
					<dd><code>{step.step_hash}</code></dd>
				</div>
			</dl>
		{/each}
	{/if}
</li>

<style>
	.step {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		padding: 0.55rem 0;
	}
	.head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 0.6rem;
	}
	.headline {
		margin: 0;
		font-size: 0.88rem;
		line-height: 1.4;
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: 0.2rem 0.75rem;
		align-items: baseline;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		cursor: pointer;
	}
	.link.quiet {
		color: var(--color-text-dim);
	}
	.link.quiet:hover {
		color: var(--color-text-muted);
	}
	.badge {
		flex: none;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
		white-space: nowrap;
	}
	.badge.problem {
		color: var(--color-status-failed);
		font-weight: 600;
	}
	dl {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin: 0.4rem 0 0;
	}
	dl + dl {
		padding-top: 0.4rem;
		border-top: 1px dashed var(--color-border-subtle);
	}
	dl > div {
		display: flex;
		flex-wrap: wrap;
		gap: 0.2rem 0.6rem;
	}
	dt {
		flex: 0 0 6rem;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
	}
	dd {
		flex: 1;
		min-width: 0;
		margin: 0;
	}
	code,
	pre {
		font-family: var(--font-mono);
		font-size: var(--text-size-caption);
		overflow-wrap: anywhere;
	}
	pre {
		margin: 0;
		padding: 0.45rem 0.55rem;
		border-radius: 0.35rem;
		background: var(--color-surface-muted);
		white-space: pre-wrap;
	}
</style>
