<script lang="ts">
	import { page } from '$app/stores';
	import { account } from '$lib/account.svelte';
	import { feedbackEnabled, submitFeedback, type FeedbackCategory } from '$lib/api';
	import { addBreadcrumb, getBreadcrumbs } from '$lib/breadcrumbs';

	let dialog = $state<HTMLDialogElement>();
	let textareaEl = $state<HTMLTextAreaElement>();
	let message = $state('');
	let category = $state<FeedbackCategory>('bug');
	let submitting = $state(false);
	let error = $state('');
	let issueUrl = $state('');

	const enabled = feedbackEnabled();

	const placeholders: Record<FeedbackCategory, string> = {
		bug: 'What happened? What did you expect to happen?',
		idea: 'What would make Almanac more useful for you?',
		other: "What's on your mind?"
	};

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
		error = '';
		issueUrl = '';
		addBreadcrumb('action', 'Opened feedback form');
		dialog?.showModal();
		textareaEl?.focus();
	}

	function close() {
		if (submitting) return;
		dialog?.close();
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
	<button class="feedback-trigger" onclick={openModal}>Feedback</button>
{/if}

<!-- Native <dialog> renders in the top layer: the nav's backdrop-filter makes
     it the containing block for fixed-position children, which clipped a
     position:fixed overlay to the nav's box. -->
<dialog
	bind:this={dialog}
	aria-label="Send feedback"
	oncancel={(e) => {
		if (submitting) e.preventDefault();
	}}
	onclick={(e) => {
		if (e.target === dialog) close();
	}}
>
	<div class="modal-body">
		{#if issueUrl}
			<h2>Thanks!</h2>
			<p class="sent">Your feedback went straight to the team.</p>
			<div class="actions">
				<button class="primary" onclick={close}>Done</button>
			</div>
		{:else}
			<h2>Send feedback</h2>
			<p class="subtitle">Spotted a bug or have an idea? Tell us — it goes straight to the team.</p>
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
					bind:this={textareaEl}
					bind:value={message}
					rows="7"
					maxlength="5000"
					placeholder={placeholders[category]}
					disabled={submitting}></textarea>
				<p class="hint">
					Reports are posted to our public GitHub issue tracker along with your recent activity in
					the app (pages visited, API calls, errors) so we can reproduce issues. Don't include
					anything you wouldn't share publicly.
				</p>
				{#if error}<p class="error">{error}</p>{/if}
				<div class="actions">
					<button type="button" onclick={close} disabled={submitting}>Cancel</button>
					<button type="submit" class="primary" disabled={submitting || !message.trim()}>
						{submitting ? 'Sending…' : 'Send feedback'}
					</button>
				</div>
			</form>
		{/if}
	</div>
</dialog>

<style>
	.feedback-trigger {
		padding: 0.45rem 0.7rem;
		border: none;
		border-radius: 0.4rem;
		background: transparent;
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.92rem;
		font-weight: 650;
		cursor: pointer;
		white-space: nowrap;
	}

	.feedback-trigger:hover {
		color: var(--color-text);
		background: var(--color-surface-muted);
	}

	dialog {
		width: min(100% - 2rem, 34rem);
		padding: 0;
		border: 0.0625rem solid var(--color-border);
		border-radius: 0.75rem;
		background: var(--color-bg, white);
		color: inherit;
	}

	dialog::backdrop {
		background: rgb(0 0 0 / 0.4);
	}

	.modal-body {
		padding: 1.25rem 1.5rem;
	}

	h2 {
		margin: 0 0 0.25rem;
		font-size: 1.1rem;
	}

	.subtitle {
		margin: 0 0 0.85rem;
		color: var(--color-text-muted);
		font-size: 0.85rem;
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
