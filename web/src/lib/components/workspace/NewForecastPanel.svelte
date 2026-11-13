<script lang="ts">
	import {
		getForecastModels,
		getInitSources,
		listBlends,
		type Blend,
		type Forecast,
		type ForecastModel,
		type InitSource
	} from '$lib/api';
	import { settled } from '$lib/api/core';
	import type { WorkspaceJob } from '$lib/api/workspaces';
	import ForecastForm from '$lib/components/forecast-setup/ForecastForm.svelte';
	import type { ForecastDraft } from '$lib/interface-actions/actions';
	import { attachFailure, latestBlendId } from '$lib/workspace/setup-start';
	import SetupPanel from './SetupPanel.svelte';

	interface Props {
		/** The workspace's runs; the form opens on the newest finished blend among them. */
		jobs: WorkspaceJob[];
		/** A setup something supplied; its blend takes the place of the workspace's newest. */
		draft?: ForecastDraft;
		/** The forecast has been submitted; resolves once it is in the workspace. */
		onStarted: (jobIds: string[]) => Promise<void>;
		onCancel: () => void;
	}

	let { jobs, draft, onStarted, onCancel }: Props = $props();

	let blends = $state<Blend[]>([]);
	let forecastModels = $state<ForecastModel[]>([]);
	let initSources = $state<InitSource[]>([]);
	let loaded = $state(false);
	let error = $state<string | null>(null);

	// The draft and the workspace as they were when the form opened.
	// svelte-ignore state_referenced_locally
	const wantedBlendId = draft?.blend_id ?? latestBlendId(jobs);
	const startBlendId = $derived(
		blends.some((blend) => blend.id === wantedBlendId) ? (wantedBlendId ?? '') : ''
	);

	async function loadChoices() {
		const [fetchedBlends, fetchedModels, fetchedSources] = await Promise.allSettled([
			listBlends(),
			getForecastModels(),
			getInitSources()
		]);
		blends = settled(fetchedBlends, []).filter((blend) => blend.status === 'complete');
		forecastModels = settled(fetchedModels, []);
		initSources = settled(fetchedSources, []);
		loaded = true;
	}
	void loadChoices();

	async function submitted(forecast: Forecast) {
		try {
			await onStarted([forecast.id]);
		} catch {
			error = attachFailure('forecast');
		}
	}
</script>

<SetupPanel
	title="New forecast"
	note="Runs a finished blend’s models forward from the latest conditions and applies the blend’s trained weights."
	{error}
	loading={!loaded}
	{onCancel}
>
	<ForecastForm
		{blends}
		{forecastModels}
		{initSources}
		{startBlendId}
		startModelNames={draft?.models}
		onSubmitted={submitted}
		{onCancel}
	/>
</SetupPanel>
