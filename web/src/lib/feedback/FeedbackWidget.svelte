<script lang="ts">
	import { page } from '$app/stores';
	import { account } from '$lib/account.svelte';
	import { feedbackEnabled, submitFeedback, type FeedbackCategory } from '$lib/api';
	import { addBreadcrumb, getBreadcrumbs } from '$lib/breadcrumbs';

	let open = $state(false);
	let message = $state('');
	let category = $state<FeedbackCategory>('bug');
	let submitting = $state(false);
	let error = $state('');
	let issueUrl = $state('');

	const enabled = feedbackEnabled();

	function snapshot(): Record<string, unknown> {
		const config = typeof window !== 'undefined' ? window.__ALMANAC_CONFIG__ : undefined;
		return {
			url: $page.url.href,
			route: $page.route?.id ?? null,
			params: $page.params,
			version: config?.version ?? 'dev',
			authMode: config?.authMode ?? null,
			deploymentMode: account.account?.deployment_mode ?? null,
			isAdmin: account.isAdmin,
			userAgent: navigator.userAgent,
			viewport: `${window.innerWidth}x${window.innerHeight}`,
			language: navigator.language,
			timestamp: new Date().toISOString()
		};
	}

	function openModal() {
		open = true;
		error = '';
		issueUrl = '';
		addBreadcrumb('action', 'Opened feedback form');
	}

	function close() {
		open = false;
		submitting = false;
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!message.trim() || submitting) return;
		submitting = true;
		error = '';
		try {
			const result = await submitFeedback({
				message: message.trim(),
				category,
				page: $page.url.pathname,
				snapshot: snapshot(),
				breadcrumbs: getBreadcrumbs()
			});
			issueUrl = result.issue_url;
			message = '';
		} catch (e) {
			error =
				e instanceof Error && e.message.includes('503')
					? 'Feedback is not configured on this deployment.'
					: 'Could not send feedback. Please try again in a moment.';
		} finally {
			submitting = false;
		}
	}
</script>

{#if enabled}
	<button class="feedback-fab" onclick={openModal} title="Send feedback"> Feedback </button>
{/if}

{#if open}
	<div
		class="overlay"
		role="presentation"
		onclick={(e) => {
			if (e.target === e.currentTarget) close();
		}}
	>
		<div class="modal" role="dialog" aria-modal="true" aria-label="Send feedback">
			{#if issueUrl}
				<h2>Thanks!</h2>
				<p class="sent">Your feedback was recorded.</p>
				<div class="actions">
					<button class="primary" onclick={close}>Done</button>
				</div>
			{:else}
				<h2>Send feedback</h2>
				<form onsubmit={submit}>
					<div class="categories" role="radiogroup" aria-label="Category">
						{#each [['bug', 'Bug'], ['idea', 'Idea'], ['other', 'Other']] as [value, label] (value)}
							<label class:selected={category === value}>
								<input type="radio" name="category" {value} bind:group={category} />
								{label}
							</label>
						{/each}
					</div>
					<textarea
						bind:value={message}
						rows="5"
						maxlength="5000"
						placeholder="What happened? What did you expect?"
						disabled={submitting}
					></textarea>
					<p class="hint">
						Your report includes your recent activity in the app (pages visited, API calls, errors)
						to help us reproduce the issue.
					</p>
					{#if error}<p class="error">{error}</p>{/if}
					<div class="actions">
						<button type="button" onclick={close} disabled={submitting}>Cancel</button>
						<button type="submit" class="primary" disabled={submitting || !message.trim()}>
							{submitting ? 'Sending…' : 'Send'}
						</button>
					</div>
				</form>
			{/if}
		</div>
	</div>
{/if}

<style>
	.feedback-fab {
		position: fixed;
		right: 1.25rem;
		bottom: 1.25rem;
		z-index: 60;
		padding: 0.55rem 1rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 2rem;
		background: var(--color-accent);
		color: white;
		font-size: 0.85rem;
		font-weight: 700;
		cursor: pointer;
		box-shadow: 0 0.25rem 0.75rem rgb(0 0 0 / 0.15);
	}

	.feedback-fab:hover {
		filter: brightness(1.08);
	}

	.overlay {
		position: fixed;
		inset: 0;
		z-index: 70;
		display: flex;
		align-items: center;
		justify-content: center;
		background: rgb(0 0 0 / 0.4);
	}

	.modal {
		width: min(100% - 2rem, 28rem);
		padding: 1.25rem 1.5rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.75rem;
		background: var(--color-bg, white);
	}

	h2 {
		margin: 0 0 0.75rem;
		font-size: 1.1rem;
	}

	.categories {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 0.75rem;
	}

	.categories label {
		padding: 0.3rem 0.8rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 2rem;
		font-size: 0.85rem;
		font-weight: 650;
		cursor: pointer;
	}

	.categories label.selected {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: white;
	}

	.categories input {
		position: absolute;
		opacity: 0;
		pointer-events: none;
	}

	textarea {
		width: 100%;
		padding: 0.6rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.5rem;
		font: inherit;
		font-size: 0.9rem;
		resize: vertical;
		box-sizing: border-box;
	}

	.hint {
		margin: 0.5rem 0 0;
		color: var(--color-text-muted);
		font-size: 0.75rem;
	}

	.error {
		margin: 0.5rem 0 0;
		color: #b0342f;
		font-size: 0.85rem;
	}

	.sent {
		margin: 0;
		font-size: 0.9rem;
	}

	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		margin-top: 1rem;
	}

	.actions button {
		padding: 0.45rem 1rem;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.5rem;
		background: transparent;
		font-size: 0.85rem;
		font-weight: 650;
		cursor: pointer;
	}

	.actions button.primary {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: white;
	}

	.actions button:disabled {
		opacity: 0.5;
		cursor: default;
	}
</style>
