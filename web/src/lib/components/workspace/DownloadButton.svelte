<script lang="ts">
	interface Props {
		/** What is being downloaded, for the accessible name. */
		label: string;
		onclick: () => void;
		/** Show the word "Download" beside the icon. */
		withText?: boolean;
	}

	let { label, onclick, withText = false }: Props = $props();
</script>

<button
	type="button"
	class="download"
	class:with-text={withText}
	aria-label={`Download ${label}`}
	title="Download"
	{onclick}
>
	<svg viewBox="0 0 24 24" aria-hidden="true">
		<path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19h14" />
	</svg>
	{#if withText}<span>Download</span>{/if}
</button>

<style>
	.download {
		display: inline-flex;
		flex: none;
		align-items: center;
		gap: 0.35rem;
		padding: 0.3rem;
		border: 1px solid transparent;
		border-radius: 0.35rem;
		background: none;
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.82rem;
		cursor: pointer;
	}
	.download:hover,
	.download:focus-visible {
		color: var(--color-accent);
		border-color: var(--color-accent-border);
	}
	.download.with-text {
		padding: 0.35rem 0.7rem;
		border-color: var(--color-border);
		color: var(--color-accent);
		background: var(--color-surface);
	}
	svg {
		width: 1rem;
		height: 1rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 2;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
</style>
