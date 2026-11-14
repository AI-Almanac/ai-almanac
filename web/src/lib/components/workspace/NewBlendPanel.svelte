<script lang="ts">
	import {
		getCapabilities,
		getGuardrailThresholds,
		listDataSources,
		type Blend,
		type DataSource
	} from '$lib/api';
	import { settled } from '$lib/api/core';
	import type { FocusAreaValue } from '$lib/api/jobs';
	import BlendForm from '$lib/components/blend-setup/BlendForm.svelte';
	import type { BlendDraft } from '$lib/interface-actions/actions';
	import { attachFailure, blendSetupFromDraft } from '$lib/workspace/setup-start';
	import { draftIn } from '$lib/workspace/study-area';
	import SetupPanel from './SetupPanel.svelte';

	interface Props {
		/** A setup something supplied, to fill the form in with. */
		draft?: BlendDraft;
		/** Where the workspace works: where a blend with no setup of its own starts. */
		area?: FocusAreaValue | null;
		/** The blend has been submitted; resolves once it is in the workspace. */
		onStarted: (jobIds: string[]) => Promise<void>;
		onCancel: () => void;
	}

	let { draft, area = null, onStarted, onCancel }: Props = $props();

	let obsSources = $state<DataSource[]>([]);
	let modelSources = $state<DataSource[]>([]);
	let dayLevelAvailable = $state(false);
	let minTrainingYears = $state<number | undefined>(undefined);
	let loaded = $state(false);
	let error = $state<string | null>(null);
	// Derived once: a new object each time would fill the form in again over the user's edits.
	const setup = $derived(draft ?? draftIn(area));
	const startFrom = $derived(setup ? blendSetupFromDraft(setup) : null);

	function ready(sources: DataSource[]): DataSource[] {
		return sources.filter((source) => source.status === 'ready');
	}

	async function loadChoices() {
		const [obs, models, capabilities, guardrails] = await Promise.allSettled([
			listDataSources('obs'),
			listDataSources('model'),
			getCapabilities(),
			getGuardrailThresholds()
		]);
		obsSources = ready(settled(obs, []));
		modelSources = ready(settled(models, []));
		dayLevelAvailable = settled(capabilities, null)?.day_level_blend ?? false;
		minTrainingYears = settled(guardrails, null)?.min_training_years;
		loaded = true;
	}

	let loading = false;
	$effect(() => {
		if (loading) return;
		loading = true;
		void loadChoices();
	});

	async function submitted(blend: Blend) {
		try {
			await onStarted([blend.id]);
		} catch {
			error = attachFailure('blend');
		}
	}
</script>

<!-- The form waits for the choices: a setup names its observations and models among them. -->
<SetupPanel
	title="New blend"
	note={draft
		? 'Filled in by the assistant. Check it over and change anything before you train it.'
		: null}
	{error}
	loading={!loaded}
	{onCancel}
>
	<BlendForm
		{obsSources}
		{modelSources}
		{dayLevelAvailable}
		{minTrainingYears}
		{startFrom}
		onSubmitted={submitted}
		{onCancel}
	/>
</SetupPanel>
