<script lang="ts">
	import FigureCard from '$lib/components/FigureCard.svelte';
	import { technicalDetails } from '$lib/chat/technical-details';
	import { artifactToFigure, copyCode } from '$lib/chat/format';
	import { renderMarkdown } from '$lib/chat/markdown';
	import { resultError } from '$lib/chat/tool-results';
	import { toolLabel } from '$lib/chat/tools';
	import {
		groupState,
		groupSummary,
		liveLabel,
		stepState,
		type ActivityGroup,
		type StepState
	} from '$lib/chat/timeline';

	interface Props {
		group: ActivityGroup;
		/** The assistant is still working in this group. */
		live: boolean;
		onOpenArtifact: (artifactId: string) => void;
		/** Off where whatever hosts the conversation shows the group's figures itself. */
		drawFigures?: boolean;
		/** Each step's tool and arguments, closed by default; without it only a code step offers its code. */
		showTechnicalDetails?: boolean;
	}

	const {
		group,
		live,
		onOpenArtifact,
		drawFigures = true,
		showTechnicalDetails = false
	}: Props = $props();

	let expanded = $state(false);

	const latestNote = $derived.by(() => {
		const last = group.steps.at(-1);
		return live && last?.kind === 'thinking' ? last.text : null;
	});

	const standing = $derived(groupState(group, live));
</script>

{#snippet mark(of: StepState)}
	{#if of === 'active'}
		<span class="spinner"></span>
	{:else if of === 'waiting'}
		<svg class="waiting-mark" viewBox="0 0 16 16"><path d="M6 4.5v7M10 4.5v7" /></svg>
	{:else if of === 'declined'}
		–
	{:else if of === 'failed'}
		!
	{:else}
		✓
	{/if}
{/snippet}

<div class="activity {standing}" class:live>
	<button class="activity-header" aria-expanded={expanded} onclick={() => (expanded = !expanded)}>
		<span class="status-icon" aria-hidden="true">{@render mark(standing)}</span>
		<span class="activity-label">
			{standing === 'active' ? `${liveLabel(group)}…` : groupSummary(group)}
		</span>
		<span class="chevron" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
	</button>

	{#if latestNote && !expanded}
		<div class="note note-preview prose-sm">{@html renderMarkdown(latestNote)}</div>
	{/if}

	{#if expanded}
		<ol class="steps">
			{#each group.steps as step, si (si)}
				{#if step.kind === 'thinking'}
					<li class="step note prose-sm">{@html renderMarkdown(step.text)}</li>
				{:else}
					{@const toolCall = step.toolCall}
					{@const details = technicalDetails(toolCall)}
					{@const stepIs = stepState(step, live)}
					<!-- A declined step's label already says it did not run. -->
					{@const error = stepIs === 'failed' ? resultError(toolCall.result) : null}
					<li class="step tool-step">
						<div class="tool-row">
							<span class="step-status" aria-hidden="true">{@render mark(stepIs)}</span>
							<span class="step-label">{toolLabel(toolCall, stepIs)}</span>
						</div>
						{#if error}
							<p class="tool-error">{error}</p>
						{/if}
						<!-- Outside a workspace only a code step has something to check: its code. -->
						{#if showTechnicalDetails || details.code}<details class="technical">
								<summary>{showTechnicalDetails ? 'Technical details' : 'Show code'}</summary>
								<dl>
									{#if showTechnicalDetails}
										<dt>Tool</dt>
										<dd><code>{details.tool}</code></dd>
										{#if details.arguments}
											<dt>Arguments</dt>
											<dd><pre class="code-block"><code>{details.arguments}</code></pre></dd>
										{/if}
									{/if}
									{#if details.code}
										{@const code = details.code}
										<dt>
											Code
											<button class="code-action-btn" onclick={() => copyCode(code)}>Copy</button>
										</dt>
										<dd><pre class="code-block"><code>{code}</code></pre></dd>
									{/if}
								</dl>
							</details>{/if}
					</li>
				{/if}
			{/each}
		</ol>
	{/if}
</div>

{#if drawFigures && group.figures.length > 0}
	<div class="figures">
		{#each group.figures as artifact (artifact.id)}
			<div class="figure">
				<FigureCard
					figure={artifactToFigure(artifact)}
					onclick={() => onOpenArtifact(artifact.id)}
				/>
			</div>
		{/each}
	</div>
{/if}

<style>
	.activity {
		display: flex;
		flex-direction: column;
		border-left: 2px solid var(--color-border);
		padding-left: 0.65rem;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}
	.activity.live {
		border-left-color: var(--color-accent);
	}
	.activity.failed {
		border-left-color: var(--color-status-failed);
	}

	.activity-header {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		background: none;
		border: 0;
		padding: 0.15rem 0;
		font: inherit;
		color: inherit;
		cursor: pointer;
		text-align: left;
	}
	.activity-header:hover {
		color: var(--color-text);
	}

	.activity-label {
		flex: 1;
		min-width: 0;
	}
	.live .activity-label {
		color: var(--color-text);
	}

	.status-icon,
	.step-status {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1rem;
		flex-shrink: 0;
		font-size: 0.75rem;
	}
	.failed .status-icon,
	.tool-error {
		color: var(--color-status-failed);
	}
	.waiting-mark {
		width: 0.85rem;
		height: 0.85rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.75;
		stroke-linecap: round;
	}

	.chevron {
		font-size: 0.7rem;
	}

	.spinner {
		width: 0.7rem;
		height: 0.7rem;
		border: 1.5px solid var(--color-border-subtle);
		border-top-color: var(--color-accent);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.note {
		font-style: italic;
		line-height: 1.5;
	}
	.note-preview {
		padding-left: 1.45rem;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.prose-sm :global(p) {
		margin: 0 0 0.35em;
	}
	.prose-sm :global(p:last-child) {
		margin-bottom: 0;
	}

	.steps {
		list-style: none;
		margin: 0.25rem 0 0;
		padding: 0 0 0 1.45rem;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.tool-step {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.tool-row {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		margin-left: -1.45rem;
	}
	.step-label {
		flex: 1;
		min-width: 0;
	}
	.tool-error {
		margin: 0;
	}

	.technical {
		font-size: var(--text-size-caption);
	}
	.technical summary {
		cursor: pointer;
		width: fit-content;
	}
	.technical summary:hover {
		color: var(--color-text);
	}
	.technical dl {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		margin: var(--space-1) 0 0;
	}
	.technical dt {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
	.technical dd {
		margin: 0;
		min-width: 0;
	}
	.technical code {
		font-family: var(--font-mono, monospace);
		color: var(--color-text);
	}

	.code-action-btn {
		padding: 0.1rem 0.45rem;
		background: none;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		font-family: inherit;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
		cursor: pointer;
	}
	.code-action-btn:hover {
		color: var(--color-accent);
		border-color: var(--color-accent);
	}

	.code-block {
		margin: 0;
		padding: 0.6rem 0.8rem;
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: 6px;
		overflow-x: auto;
		font-family: var(--font-mono, monospace);
		font-size: var(--text-size-caption);
		line-height: 1.5;
		color: var(--color-text);
		white-space: pre;
	}

	.figures {
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
	}
	.figure {
		flex: 1 1 18rem;
		min-width: 0;
		max-width: 100%;
	}
</style>
