<script lang="ts">
	import type { SetupWizardState } from '$lib/setup/wizard.svelte';
	import { testLlm, saveLlm } from '$lib/api/setup';
	import type { LlmTestOut } from '$lib/api/setup';

	interface Props {
		wizard: SetupWizardState;
	}
	const { wizard }: Props = $props();

	let baseUrl = $state('http://localhost:11434/v1');
	let model = $state('');
	let initialized = false;
	$effect(() => {
		if (!initialized) {
			const llm = wizard.state?.llm as
				{ configured?: boolean; base_url?: string; model?: string } | undefined;
			if (llm?.base_url) baseUrl = llm.base_url;
			if (llm?.model) model = llm.model;
			initialized = true;
		}
	});
	let apiKey = $state('');
	let testing = $state(false);
	let saving = $state(false);
	let testResult = $state<LlmTestOut | null>(null);

	async function runTest() {
		testing = true;
		wizard.llmError = null;
		testResult = null;
		try {
			testResult = await testLlm({
				base_url: baseUrl,
				model,
				api_key: apiKey || null,
				test_only: true
			});
		} catch (e) {
			wizard.llmError = e instanceof Error ? e.message : String(e);
		} finally {
			testing = false;
		}
	}

	async function save() {
		saving = true;
		wizard.llmError = null;
		try {
			const result = await saveLlm({
				base_url: baseUrl,
				model,
				api_key: apiKey || null,
				test_only: false
			});
			if (!result.ok) {
				wizard.llmError = result.error ?? 'LLM connection failed — check the URL and model name';
				return;
			}
			wizard.goNext();
		} catch (e) {
			wizard.llmError = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

<div class="card">
	<h2>LLM endpoint</h2>
	<p class="lede">
		Connect AI Almanac to a local or remote OpenAI-compatible model server (Ollama, vLLM, etc.).
	</p>

	<div class="fields">
		<div class="field">
			<label for="base-url">Base URL</label>
			<input
				id="base-url"
				type="text"
				bind:value={baseUrl}
				placeholder="http://localhost:11434/v1"
			/>
		</div>
		<div class="field">
			<label for="model">Model name</label>
			<input id="model" type="text" bind:value={model} placeholder="llama3" />
		</div>
		<div class="field">
			<label for="api-key">API key <span class="optional">(optional)</span></label>
			<input
				id="api-key"
				type="password"
				bind:value={apiKey}
				placeholder="Leave blank if not required"
			/>
		</div>
	</div>

	{#if testResult}
		<div class="test-result" class:ok={testResult.ok} class:fail={!testResult.ok}>
			{#if testResult.ok}
				<strong>✓ Connected</strong>
				{#if testResult.models.length}
					<p>Available models: {testResult.models.join(', ')}</p>
				{/if}
			{:else}
				<strong>✗ Connection failed</strong>
				<p>{testResult.error}</p>
			{/if}
		</div>
	{/if}

	{#if wizard.llmError}
		<p class="error">{wizard.llmError}</p>
	{/if}

	<div class="actions">
		<button class="secondary" onclick={() => wizard.goPrev()}>← Back</button>
		<button class="secondary" onclick={runTest} disabled={testing || !baseUrl || !model}>
			{testing ? 'Testing…' : 'Test connection'}
		</button>
		<button onclick={save} disabled={saving || !baseUrl || !model}>
			{saving ? 'Saving…' : 'Save & continue →'}
		</button>
	</div>
</div>

<style>
	.card {
		border: 1px solid var(--color-border);
		border-radius: 0.7rem;
		background: var(--color-surface-raised);
		padding: 1.25rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	h2 {
		margin: 0;
		font-size: 1.05rem;
	}
	.lede {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}
	.fields {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	label {
		font-weight: 600;
		font-size: 0.9rem;
	}
	.optional {
		font-weight: 400;
		font-size: 0.8em;
		color: var(--color-text-muted);
	}
	input {
		padding: 0.45rem 0.6rem;
		border-radius: 0.4rem;
		border: 1px solid var(--color-border);
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: 0.85rem;
		width: 100%;
		box-sizing: border-box;
	}
	.test-result {
		padding: 0.75rem 1rem;
		border-radius: 0.45rem;
		font-size: 0.85rem;
	}
	.test-result.ok {
		background: var(--color-status-done-bg, #e6f4ea);
		border: 1px solid var(--color-status-done, #34a853);
	}
	.test-result.fail {
		background: var(--color-status-failed-bg, #fce8e6);
		border: 1px solid var(--color-status-failed, #d93025);
	}
	.test-result strong {
		display: block;
		margin-bottom: 0.2rem;
	}
	.test-result p {
		margin: 0;
	}
	.error {
		margin: 0;
		font-size: 0.85rem;
		color: var(--color-status-failed, #c00);
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.75rem;
		flex-wrap: wrap;
	}
	button {
		padding: 0.5rem 1.1rem;
		border-radius: 0.45rem;
		border: 1px solid var(--color-border);
		background: var(--color-accent);
		color: #fff;
		font: inherit;
		font-weight: 600;
		font-size: 0.9rem;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	button.secondary {
		background: transparent;
		color: var(--color-text-muted);
	}
</style>
