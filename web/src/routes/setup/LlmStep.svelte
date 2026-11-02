<script lang="ts">
	import type { SetupWizardState } from '$lib/setup/wizard.svelte';
	import { testLlm, saveLlm } from '$lib/api/setup';
	import type { LlmTestOut, SetupState } from '$lib/api/setup';

	interface Props {
		wizard: SetupWizardState;
	}
	const { wizard }: Props = $props();

	type LlmState = {
		configured?: boolean;
		engine?: 'external' | 'managed';
		base_url?: string;
		model?: string;
		default_local_model?: string;
	};
	type GpuInfo = { name?: string } | null | undefined;
	type PlatformInfo = { platform?: string } | undefined;

	const CUSTOM = '__custom__';

	// "managed": ai-almanac runs the model itself with a bundled vLLM.
	let engine = $state<'external' | 'managed'>('external');
	let baseUrl = $state('http://localhost:11434/v1');
	let model = $state('');
	// A catalog model id, or CUSTOM for any other Hugging Face id.
	let localChoice = $state('');
	let customModel = $state('');
	const localModels = $derived<SetupState['local_models']>(wizard.state?.local_models ?? []);
	const localModel = $derived(localChoice === CUSTOM ? customModel.trim() : localChoice);
	let initialized = false;
	$effect(() => {
		if (!initialized) {
			const llm = wizard.state?.llm as LlmState | undefined;
			const gpu = wizard.state?.gpu as GpuInfo;
			const platform = wizard.state?.platform as PlatformInfo;
			const canRunLocal = Boolean(gpu) && (platform?.platform ?? '').startsWith('linux');
			engine = llm?.configured ? (llm.engine ?? 'external') : canRunLocal ? 'managed' : 'external';
			let current = llm?.default_local_model ?? '';
			if (llm?.engine === 'managed' && llm.model) current = llm.model;
			else {
				if (llm?.base_url) baseUrl = llm.base_url;
				if (llm?.model) model = llm.model;
			}
			if (localModels.some((m) => m.id === current)) localChoice = current;
			else {
				localChoice = CUSTOM;
				customModel = current;
			}
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
				test_only: true,
				engine: 'external'
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
			const result = await saveLlm(
				engine === 'managed'
					? { engine, base_url: '', model: localModel, test_only: false }
					: { engine, base_url: baseUrl, model, api_key: apiKey || null, test_only: false }
			);
			if (!result.ok) {
				wizard.llmError = result.error ?? 'LLM connection failed — check the URL and model name';
				return;
			}
			// The envs step reads the saved engine to decide whether to install
			// the local model server.
			await wizard.load();
			wizard.goTo('envs');
		} catch (e) {
			wizard.llmError = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

<div class="card">
	<h2>Assistant model</h2>
	<p class="lede">
		Run a model on this machine, or connect AI Almanac to an OpenAI-compatible model server you
		already have (Ollama, vLLM, etc.).
	</p>

	<div class="choices" role="radiogroup" aria-label="Assistant model">
		<label class="choice">
			<input type="radio" bind:group={engine} value="managed" />
			<span>
				<strong>Run a model on this machine</strong>
				<span class="desc">
					Installs vLLM and downloads the model's weights in the next step. Needs an NVIDIA GPU on
					Linux. The assistant pauses while a forecast runs so both fit in memory.
				</span>
			</span>
		</label>
		<label class="choice">
			<input type="radio" bind:group={engine} value="external" />
			<span>
				<strong>Connect to an existing server</strong>
				<span class="desc">Any OpenAI-compatible endpoint, local or remote.</span>
			</span>
		</label>
	</div>

	{#if engine === 'managed'}
		<div class="choices" role="radiogroup" aria-label="Local model">
			{#each localModels as m (m.id)}
				<label class="choice">
					<input type="radio" bind:group={localChoice} value={m.id} />
					<span>
						<strong>{m.label}</strong>
						<span class="badge">{m.weights_gb.toFixed(0)} GB</span>
						{#if m.downloaded}<span class="badge">downloaded</span>{/if}
						{#if !m.verified}<span class="badge warn">not yet tested on a Spark</span>{/if}
						<span class="desc">{m.description}</span>
						<code class="desc">{m.id}</code>
					</span>
				</label>
			{/each}
			<label class="choice">
				<input type="radio" bind:group={localChoice} value={CUSTOM} />
				<span>
					<strong>Other model</strong>
					<span class="desc">
						Any Hugging Face model vLLM can serve. Set its tool-call parser under Settings → AI
						assistant → Local model server flags, or the assistant's tools won't work.
					</span>
				</span>
			</label>
		</div>
		{#if localChoice === CUSTOM}
			<div class="fields">
				<div class="field">
					<label for="local-model">Hugging Face model id</label>
					<input
						id="local-model"
						type="text"
						bind:value={customModel}
						placeholder="org/model-name"
					/>
				</div>
			</div>
		{/if}
	{:else}
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
	{/if}

	{#if testResult && engine === 'external'}
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
		{#if engine === 'external'}
			<button class="secondary" onclick={runTest} disabled={testing || !baseUrl || !model}>
				{testing ? 'Testing…' : 'Test connection'}
			</button>
		{/if}
		<button
			onclick={save}
			disabled={saving || (engine === 'managed' ? !localModel : !baseUrl || !model)}
		>
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
	.choices {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.choice {
		display: flex;
		gap: 0.6rem;
		align-items: flex-start;
		padding: 0.6rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.45rem;
		cursor: pointer;
		font-weight: 400;
	}
	.choice input {
		width: auto;
		margin-top: 0.2rem;
	}
	.choice .desc {
		display: block;
		font-size: 0.8rem;
		color: var(--color-text-muted);
	}
	.choice code.desc {
		font-size: 0.75rem;
	}
	.badge {
		margin-left: 0.4rem;
		padding: 0.05rem 0.4rem;
		border-radius: 0.3rem;
		border: 1px solid var(--color-border);
		font-size: 0.72rem;
		color: var(--color-text-muted);
	}
	.badge.warn {
		border-color: var(--color-status-failed, #d93025);
		color: var(--color-status-failed, #d93025);
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
