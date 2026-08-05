<script lang="ts">
	// Rendered above the nav for the whole app: an admin previewing the user
	// view will land on "Administrators only" pages, so the way out cannot live
	// inside anything admin-gated.
	import { account } from '$lib/account.svelte';
</script>

<!-- Gated on the preview flag alone, never on the loaded account: only an admin
     can turn this on, and a failed /auth/me must not take the exit with it. -->
{#if account.viewingAsUser}
	<div class="preview-bar" role="status">
		<span>
			<strong>Viewing as a regular user.</strong>
			Admin-only navigation and controls are hidden. Server permissions are unchanged, and reloading the
			page ends the preview.
		</span>
		<button onclick={() => account.setViewingAsUser(false)}>Exit preview</button>
	</div>
{/if}

<style>
	.preview-bar {
		position: sticky;
		top: 0;
		z-index: 60;
		display: flex;
		align-items: center;
		justify-content: center;
		flex-wrap: wrap;
		gap: 0.75rem;
		padding: 0.45rem 0.85rem;
		background: var(--color-status-running-bg);
		color: var(--color-status-running);
		border-bottom: 1px solid var(--color-status-running);
		font-size: 0.78rem;
		line-height: 1.4;
		text-align: center;
	}
	button {
		padding: 0.25rem 0.7rem;
		border: 1px solid currentColor;
		border-radius: 999px;
		background: transparent;
		color: inherit;
		font: inherit;
		font-weight: 600;
		cursor: pointer;
		white-space: nowrap;
	}
	button:hover {
		background: color-mix(in oklab, currentColor 12%, transparent);
	}
</style>
