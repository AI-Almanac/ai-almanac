<script lang="ts">
	let { code, capped = false }: { code: string; capped?: boolean } = $props();

	const lines = $derived(code.replace(/\n$/, '').split('\n'));
	let copied = $state(false);

	async function copy() {
		await navigator.clipboard.writeText(code);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}
</script>

<div class="code" class:capped>
	<button type="button" class="copy" onclick={copy}>{copied ? 'Copied' : 'Copy'}</button>
	<pre><code
			>{#each lines as line, index (index)}<span class="line">{line}</span>{'\n'}{/each}</code
		></pre>
</div>

<style>
	.code {
		position: relative;
		display: flex;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.4rem;
		background: var(--color-surface-muted);
	}
	.capped {
		max-height: 24rem;
		overflow-y: auto;
	}
	pre {
		flex: 1;
		min-width: 0;
		margin: 0;
		padding: 0.6rem 0;
		overflow-x: auto;
		font-family: var(--font-mono);
		font-size: var(--text-size-caption);
		line-height: 1.55;
		counter-reset: line;
	}
	.line {
		padding-right: 1rem;
	}
	.line::before {
		counter-increment: line;
		content: counter(line);
		display: inline-block;
		width: 3ch;
		margin-right: 1.25ch;
		padding-left: 0.6rem;
		text-align: right;
		color: var(--color-text-dim);
		user-select: none;
	}
	.copy {
		position: sticky;
		top: 0.4rem;
		align-self: flex-start;
		order: 2;
		margin: 0.4rem 0.4rem 0 0;
		padding: 0.15rem 0.55rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.3rem;
		background: var(--color-surface-raised);
		color: var(--color-text-muted);
		font: inherit;
		font-size: var(--text-size-caption);
		cursor: pointer;
	}
	.copy:hover {
		color: var(--color-text);
	}
</style>
