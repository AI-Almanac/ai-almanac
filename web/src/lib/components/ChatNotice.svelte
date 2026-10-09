<script lang="ts" module>
	export type NoticeAction = {
		label: string;
		onclick: () => void;
		/** `primary` commits to something; `suggest` sends a message; `quiet` backs off. */
		emphasis?: 'primary' | 'suggest' | 'quiet';
	};
</script>

<script lang="ts">
	interface Props {
		tone?: 'neutral' | 'success' | 'failed';
		icon?: string;
		title: string;
		detail?: string;
		actions?: NoticeAction[];
	}

	const { tone = 'neutral', icon, title, detail, actions = [] }: Props = $props();
</script>

<div class="notice {tone}" role="status">
	{#if icon}<span class="icon" aria-hidden="true">{icon}</span>{/if}
	<span class="title">{title}</span>
	{#if detail}<span class="detail">{detail}</span>{/if}
	{#each actions as action (action.label)}
		<button class="action {action.emphasis ?? 'suggest'}" onclick={action.onclick}>
			{action.label}
		</button>
	{/each}
</div>

<style>
	.notice {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-self: center;
		max-width: 100%;
		padding: 0.3rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 2rem;
		background: var(--color-surface);
		font-size: 0.75rem;
		color: var(--color-text-muted);
	}
	.notice.success .icon {
		color: var(--color-accent);
	}
	.notice.failed {
		border-color: var(--color-status-failed);
		background: var(--color-status-failed-bg);
	}
	.notice.failed .icon,
	.notice.failed .title {
		color: var(--color-status-failed);
	}
	.icon {
		font-weight: 700;
	}
	.title {
		color: var(--color-text);
		font-weight: 600;
	}
	.action {
		padding: 0.15rem 0.6rem;
		border: 1px solid var(--color-accent-border);
		border-radius: 2rem;
		background: var(--color-accent-light);
		color: var(--color-accent);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
		transition:
			background-color 0.12s,
			color 0.12s,
			border-color 0.12s;
	}
	.action.suggest:hover,
	.action.primary {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: var(--color-bg);
	}
	.action.primary:hover {
		filter: brightness(1.1);
	}
	.action.quiet {
		border-color: var(--color-border);
		background: transparent;
		color: var(--color-text-muted);
	}
	.action.quiet:hover {
		color: var(--color-text);
		border-color: var(--color-text-muted);
	}
</style>
