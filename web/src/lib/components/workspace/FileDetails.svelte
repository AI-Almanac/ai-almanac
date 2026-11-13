<script lang="ts">
	import type { WorkspaceArtifact } from '$lib/api';
	import { midnightAsDate } from '$lib/format/dates';
	import { parseFileSummary } from '$lib/workspace/file-summary';
	import { griddedFile } from '$lib/workspace/gridded-file';

	let { file }: { file: WorkspaceArtifact } = $props();

	const summary = $derived(parseFileSummary(file.summary));
	// A gridded file's variables are already described in plain words above the details.
	const describesVariables = $derived(griddedFile(file) === null);
	const hasSchema = $derived(summary.dimensions.length > 0 || summary.variables.length > 0);
</script>

<dl class="facts">
	{#if summary.timeRange}
		<div>
			<dt>Time range</dt>
			<dd>
				{midnightAsDate(summary.timeRange.start)} – {midnightAsDate(summary.timeRange.end)}
				{#if summary.timeRange.steps}<span class="dim">({summary.timeRange.steps} steps)</span>{/if}
			</dd>
		</div>
	{/if}
	{#if summary.imageSize}
		<div>
			<dt>Image size</dt>
			<dd>{summary.imageSize.width} × {summary.imageSize.height}</dd>
		</div>
	{/if}
</dl>

{#if hasSchema}
	<details class="technical">
		<summary>Technical details</summary>
		<div class="schema">
			{#if summary.dimensions.length > 0}
				<section>
					<h4>Dimensions</h4>
					<p class="chips">
						{#each summary.dimensions as [name, size] (name)}
							<span class="chip"><code>{name}</code> {size}</span>
						{/each}
					</p>
				</section>
			{/if}
			{#if summary.variables.length > 0}
				<section>
					<h4>Variables ({summary.variables.length})</h4>
					<ul>
						{#each summary.variables as variable (variable.name)}
							<li>
								<code>{variable.name}</code>
								{#if variable.units}<span class="dim">{variable.units}</span>{/if}
								{#if describesVariables && variable.description}
									<span>{variable.description}</span>
								{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/if}
		</div>
	</details>
{/if}

<style>
	.facts {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
	}
	.facts:empty {
		display: none;
	}
	.facts > div {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1) var(--space-3);
		align-items: baseline;
	}
	dt {
		flex: 0 0 7rem;
		font-size: var(--text-size-caption);
		color: var(--color-text-muted);
	}
	dd {
		flex: 1;
		min-width: 0;
		margin: 0;
		font-size: var(--text-size-body);
	}
	summary {
		font-size: var(--text-size-label);
		color: var(--color-text-muted);
		cursor: pointer;
	}
	summary:hover,
	.technical[open] summary {
		color: var(--color-text);
	}
	.schema {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		padding-top: var(--space-2);
	}
	section {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin: 0;
	}
	.chip {
		padding: 0.05rem 0.45rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 999px;
		background: var(--color-surface);
		font-size: var(--text-size-caption);
	}
	h4 {
		margin: 0;
		font-size: var(--text-size-caption);
		font-weight: 600;
		color: var(--color-text-muted);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	li {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1) var(--space-3);
		align-items: baseline;
		font-size: var(--text-size-label);
	}
	code {
		font-family: var(--font-mono);
		font-size: var(--text-size-caption);
	}
	.dim {
		margin: 0;
		color: var(--color-text-dim);
		font-size: var(--text-size-label);
	}
</style>
