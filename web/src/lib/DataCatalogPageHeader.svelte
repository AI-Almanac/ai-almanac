<script lang="ts">
	interface Props {
		eyebrow: string;
		title: string;
		description: string;
	}

	const { eyebrow, title, description }: Props = $props();
</script>

<header class="page-header">
	<p class="eyebrow">{eyebrow}</p>
	<h2>{title}</h2>
	<p class="description">{description}</p>
</header>

<style>
	.page-header {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		min-height: 7.4rem;
		padding-bottom: 0.35rem;
	}
	.eyebrow,
	h2,
	.description {
		margin: 0;
	}
	.eyebrow {
		color: var(--color-accent);
		font-size: 0.72rem;
		font-weight: 750;
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}
	h2 {
		font-family: var(--font-display);
		font-size: 1.85rem;
		font-weight: 550;
		letter-spacing: -0.025em;
		line-height: 1.2;
	}
	.description {
		max-width: 52rem;
		color: var(--color-text-muted);
	}
	@media (max-width: 700px) {
		.page-header {
			min-height: 0;
		}
	}
</style>
