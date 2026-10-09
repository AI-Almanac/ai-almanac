<script lang="ts">
	import FigureCard from '$lib/components/FigureCard.svelte';
	import {
		artifactToFigure,
		codeForToolCall,
		copyCode,
		renderMarkdown,
		toolLabel
	} from '$lib/chat/format';
	import {
		groupFailed,
		groupSummary,
		isStepActive,
		liveLabel,
		type ActivityGroup
	} from '$lib/chat/timeline';

	interface Props {
		group: ActivityGroup;
		/** The assistant is still working in this group. */
		live: boolean;
		onOpenArtifact: (artifactId: string) => void;
	}

	const { group, live, onOpenArtifact }: Props = $props();

	let expanded = $state(false);
	let shownCode = $state<Set<string>>(new Set());

	const latestNote = $derived.by(() => {
		const last = group.steps.at(-1);
		return live && last?.kind === 'thinking' ? last.text : null;
	});

	function toggleCode(id: string) {
		const next = new Set(shownCode);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		shownCode = next;
	}

	function errorText(result: unknown): string | null {
		if (!result || typeof result !== 'object' || !('error' in result)) return null;
		const error = (result as { error?: unknown }).error;
		return typeof error === 'string' ? error : null;
	}
</script>

<div class="activity" class:live class:failed={!live && groupFailed(group)}>
	<button class="activity-header" aria-expanded={expanded} onclick={() => (expanded = !expanded)}>
		<span class="status-icon" aria-hidden="true">
			{#if live}<span class="spinner"></span>{:else if groupFailed(group)}!{:else}✓{/if}
		</span>
		<span class="activity-label">{live ? `${liveLabel(group)}…` : groupSummary(group)}</span>
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
					{@const code = codeForToolCall(toolCall)}
					{@const error = errorText(toolCall.result)}
					<li class="step tool-step">
						<div class="tool-row">
							<span class="step-status" aria-hidden="true">
								{#if isStepActive(step) && live}
									<span class="spinner"></span>
								{:else if toolCall.status === 'failed'}!{:else}✓{/if}
							</span>
							<span class="tool-name">{toolLabel(toolCall)}</span>
							{#if code}
								<span class="tool-actions">
									<button class="code-action-btn" onclick={() => copyCode(code)}>Copy</button>
									<button class="code-action-btn" onclick={() => toggleCode(toolCall.id)}>
										{shownCode.has(toolCall.id) ? 'Hide code' : 'Show code'}
									</button>
								</span>
							{/if}
						</div>
						{#if error}
							<p class="tool-error">{error}</p>
						{/if}
						{#if code && shownCode.has(toolCall.id)}
							<pre class="code-block"><code>{code}</code></pre>
						{/if}
					</li>
				{/if}
			{/each}
		</ol>
	{/if}
</div>

{#if group.figures.length > 0}
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
	.tool-name {
		flex: 1;
		min-width: 0;
	}
	.tool-actions {
		display: flex;
		gap: 0.35rem;
	}
	.tool-error {
		margin: 0;
	}

	.code-action-btn {
		padding: 0.1rem 0.45rem;
		background: none;
		border: 1px solid var(--color-border);
		border-radius: 3px;
		font-family: inherit;
		font-size: 0.68rem;
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
		font-size: 0.75rem;
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
