<script lang="ts">
	let { text }: { text: string } = $props();
</script>

<!-- Not a <button>: inside a <label> it would become the labelled control and
     steal the label from the input. A focusable span keeps keyboard access. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<span class="info-tip" role="note" tabindex="0" aria-label={text} data-tip={text}>i</span>

<style>
	.info-tip {
		position: relative;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.1em;
		height: 1.1em;
		flex-shrink: 0;
		border: 1px solid var(--color-border);
		border-radius: 50%;
		font-family: Georgia, 'Times New Roman', serif;
		font-size: 0.75rem;
		font-style: italic;
		font-weight: 700;
		line-height: 1;
		color: var(--color-text-muted);
		cursor: help;
		vertical-align: middle;
		transition:
			color 0.12s,
			border-color 0.12s;
	}
	.info-tip:hover,
	.info-tip:focus-visible {
		color: var(--color-accent);
		border-color: var(--color-accent);
		outline: none;
	}
	.info-tip::after {
		content: attr(data-tip);
		position: absolute;
		bottom: calc(100% + 0.4rem);
		left: 50%;
		transform: translateX(-50%);
		width: max-content;
		max-width: min(18rem, 70vw);
		padding: 0.45rem 0.6rem;
		background: var(--color-surface-raised);
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.35rem;
		box-shadow: 0 0.25rem 0.75rem rgba(0, 0, 0, 0.18);
		font-family: var(--font-sans, inherit);
		font-size: 0.72rem;
		font-style: normal;
		font-weight: 400;
		line-height: 1.45;
		color: var(--color-text-muted);
		text-align: left;
		white-space: normal;
		pointer-events: none;
		opacity: 0;
		transition: opacity 0.15s;
		z-index: 1000;
	}
	.info-tip:hover::after,
	.info-tip:focus-visible::after {
		opacity: 1;
	}
</style>
