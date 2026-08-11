<script lang="ts">
	import { feedbackEnabled } from '$lib/api';

	const canFeedback = feedbackEnabled();

	function openFeedback() {
		window.dispatchEvent(new CustomEvent('almanac-open-feedback'));
	}
</script>

<footer class="mt-auto px-7 pt-0 pb-6">
	<div class="footer-rule mb-5 flex items-center justify-center gap-3">
		<span
			class="block h-px w-16 bg-gradient-to-r from-transparent via-[var(--color-border)] to-transparent"
		></span>
		<span class="text-[0.45rem] text-[var(--color-text-dim)] opacity-50">&#x25C7;</span>
		<span
			class="block h-px w-16 bg-gradient-to-r from-transparent via-[var(--color-border)] to-transparent"
		></span>
	</div>
	<div class="mx-auto mb-3 flex max-w-[1160px] items-center justify-center">
		<p class="supported-by">
			Supported by the <a href="https://laude.org" target="_blank" rel="noopener noreferrer"
				>Laude Institute</a
			>
		</p>
	</div>
	<div class="mx-auto mb-5 flex max-w-[1160px] flex-wrap items-center justify-center gap-8">
		<a href="https://uchicago.edu" target="_blank" rel="noopener noreferrer" class="partner-link">
			<img src="/partners/uchicago.png" alt="University of Chicago" class="partner-logo" />
		</a>
		<a href="https://globus.org" target="_blank" rel="noopener noreferrer" class="partner-link">
			<img src="/partners/globus.png" alt="Globus" class="partner-logo" />
		</a>
		<a href="https://nvidia.com" target="_blank" rel="noopener noreferrer" class="partner-link">
			<img src="/partners/nvidia.png" alt="NVIDIA" class="partner-logo" />
		</a>
		<a href="https://aws.amazon.com" target="_blank" rel="noopener noreferrer" class="partner-link">
			<img src="/partners/aws.png" alt="Amazon Web Services" class="partner-logo" />
		</a>
		<a
			href="https://cloud.google.com"
			target="_blank"
			rel="noopener noreferrer"
			class="partner-link gcp-text"
		>
			Built with Google Cloud
		</a>
	</div>
	<div
		class="mx-auto flex max-w-[1160px] items-center justify-between text-[0.75rem] tracking-[0.02em] text-[var(--color-text-dim)]"
	>
		<div class="flex items-center justify-around">
			<span class="brand-text">AI Almanac</span>
			{#if canFeedback}
				<span class="m-4">
					This platform is under active development. Encountered a bug or want to request a feature?
					Submit
					<button class="footer-feedback-link" onclick={openFeedback}>feedback</button>.
				</span>
			{/if}
		</div>
		<span>&copy; {new Date().getFullYear()} &mdash; University of Chicago</span>
	</div>
</footer>

<style>
	.brand-text {
		font-family: var(--font-display);
		font-size: 0.95rem;
		font-weight: 400;
		font-style: italic;
		color: var(--color-accent);
		opacity: 0.5;
	}

	.supported-by {
		margin: 0;
		font-size: 0.75rem;
		color: var(--color-text-dim);
	}

	.supported-by a {
		color: var(--color-text-dim);
		text-decoration: underline;
		text-underline-offset: 0.2em;
		text-decoration-color: var(--color-border);
	}

	.supported-by a:hover {
		color: var(--color-text-muted);
	}

	.partner-link {
		display: flex;
		align-items: center;
		opacity: 0.65;
		transition: opacity 0.2s ease;
	}

	.partner-link:hover {
		opacity: 1;
	}

	.partner-logo {
		height: 2.5rem;
		width: auto;
		object-fit: contain;
	}

	.gcp-text {
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--color-text-dim);
		text-decoration: none;
		letter-spacing: 0.01em;
	}

	.footer-feedback-link {
		padding: 0;
		border: none;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: inherit;
		cursor: pointer;
		text-decoration: underline;
		text-underline-offset: 0.2em;
	}

	.footer-feedback-link:hover {
		color: var(--color-accent-hover);
	}
</style>
