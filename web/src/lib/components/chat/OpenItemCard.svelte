<script lang="ts">
	import type { ReplyCard } from '$lib/chat/reply-cards';

	interface Props {
		card: ReplyCard;
		onOpen: () => void;
	}

	const { card, onOpen }: Props = $props();

	const ICONS: Record<ReplyCard['kind'], string> = { Figure: '▦', File: '▤' };
</script>

<button type="button" class="open-item" aria-label="Open {card.name}" onclick={onOpen}>
	<span class="icon" aria-hidden="true">{ICONS[card.kind]}</span>
	<span class="name">{card.name}</span>
	<span class="kind">{card.kind}</span>
</button>

<style>
	.open-item {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		width: 100%;
		min-width: 0;
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--color-border);
		border-radius: 0.375rem;
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: var(--text-size-label);
		text-align: left;
		cursor: pointer;
		transition:
			border-color 0.12s,
			background-color 0.12s;
	}
	.open-item:hover {
		border-color: var(--color-accent);
		background: var(--color-accent-light);
	}
	.icon {
		flex-shrink: 0;
		color: var(--color-accent);
	}
	.name {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-weight: 500;
	}
	.kind {
		flex-shrink: 0;
		color: var(--color-text-muted);
		font-size: var(--text-size-caption);
	}
</style>
