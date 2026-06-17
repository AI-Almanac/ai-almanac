<script lang="ts">
	import { login } from './auth';

	interface Props {
		message?: string;
	}

	let { message = 'Sign in to continue.' }: Props = $props();

	function handleLogin() {
		login(window.location.pathname + window.location.search);
	}
</script>

<section class="login-prompt">
	<p>{message}</p>
	<button type="button" onclick={handleLogin}>Sign in with Globus</button>
</section>

<style>
	.login-prompt {
		min-height: calc(100vh - 9rem);
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 1rem;
		padding: 2rem;
		text-align: center;
	}

	p {
		margin: 0;
		color: var(--color-text-muted);
		font-size: clamp(1rem, 2vw, 1.2rem);
	}

	button {
		border: 0;
		border-radius: 0.45rem;
		background: var(--color-accent);
		color: white;
		cursor: pointer;
		font-weight: 750;
		padding: 0.7rem 1.15rem;
	}

	button:hover {
		filter: brightness(0.95);
	}
</style>
