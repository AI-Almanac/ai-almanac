<script lang="ts">
	import type { AssistantStartup } from '$lib/api';
	import { formatElapsed, startupOverall, startupSteps } from '$lib/chat/startup';

	let { progress }: { progress: AssistantStartup } = $props();

	const steps = $derived(startupSteps(progress));
	const elapsed = $derived(formatElapsed(progress.elapsed_seconds));
	const overall = $derived(startupOverall(progress));
</script>

<div class="startup" role="status" aria-live="polite">
	<div class="heading">
		<strong>Starting the assistant</strong>
		{#if elapsed}<span class="elapsed">{elapsed}</span>{/if}
	</div>
	{#if overall}
		<div class="overall">
			<span
				class="bar overall-bar"
				role="progressbar"
				aria-label="Overall startup progress"
				aria-valuemin={0}
				aria-valuemax={100}
				aria-valuenow={Math.round(overall.fraction * 100)}
				><span style={`width: ${Math.round(overall.fraction * 100)}%`}></span></span
			>
			<span class="overall-label">{overall.label}</span>
		</div>
	{/if}
	<p class="note">Your message is queued and will be answered as soon as the model is ready.</p>
	{#if progress.previous_failure}
		<p class="retry">The last start failed ({progress.previous_failure}); trying again.</p>
	{/if}
	<ol>
		{#each steps as step (step.label)}
			<li class={step.state}>
				<span class="marker" aria-hidden="true"
					>{step.state === 'done' ? '✓' : step.state === 'current' ? '›' : '·'}</span
				>
				<div class="step">
					<span class="label">{step.label}</span>
					{#if step.detail}
						<span class="detail">{step.detail}</span>
					{/if}
					{#if step.fraction !== undefined}
						<span class="bar"
							><span style={`width: ${Math.round(step.fraction * 100)}%`}></span></span
						>
					{/if}
					{#if step.hint}
						<span class="hint">{step.hint}</span>
					{/if}
				</div>
			</li>
		{/each}
	</ol>
</div>

<style>
	.startup {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		padding: 0.75rem 0.9rem;
		border: 1px solid var(--color-border);
		border-radius: 0.6rem;
		background: var(--color-surface-raised);
		font-size: 0.85rem;
		max-width: 28rem;
	}
	.heading {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 1rem;
	}
	.elapsed {
		color: var(--color-text-muted);
		font-variant-numeric: tabular-nums;
	}
	.note,
	.retry {
		margin: 0;
		color: var(--color-text-muted);
		line-height: 1.35;
	}
	.retry {
		color: var(--color-status-running, var(--color-text-muted));
	}
	ol {
		list-style: none;
		margin: 0.2rem 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	li {
		display: flex;
		gap: 0.5rem;
		color: var(--color-text-muted);
	}
	li.done {
		color: var(--color-text);
	}
	li.current {
		color: var(--color-text);
		font-weight: 600;
	}
	.marker {
		width: 0.9rem;
		flex-shrink: 0;
		text-align: center;
	}
	li.done .marker {
		color: var(--color-accent);
	}
	.step {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		flex: 1;
		min-width: 0;
	}
	.detail,
	.hint {
		font-weight: 400;
		color: var(--color-text-muted);
		font-size: 0.8rem;
	}
	.overall {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.bar.overall-bar {
		height: 0.45rem;
	}
	.overall-label {
		color: var(--color-text-muted);
		font-size: 0.8rem;
	}
	.bar {
		height: 0.3rem;
		border-radius: 999px;
		background: var(--color-border);
		overflow: hidden;
	}
	.bar > span {
		display: block;
		height: 100%;
		background: var(--color-accent);
		transition: width 0.4s ease;
	}
</style>
