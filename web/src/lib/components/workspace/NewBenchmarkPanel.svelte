<script lang="ts">
	import { onDestroy } from 'svelte';
	import {
		getDatasets,
		getGuardrailThresholds,
		getJob,
		getRegions,
		getRompDefaults,
		type Dataset,
		type GuardrailThresholds,
		type Region,
		type RompDefaults
	} from '$lib/api';
	import { settled } from '$lib/api/core';
	import type { FocusAreaValue } from '$lib/api/jobs';
	import type { WorkspaceJob } from '$lib/api/workspaces';
	import { BenchmarkStore } from '$lib/benchmarks.svelte';
	import BenchmarkForm from '$lib/components/benchmark-setup/BenchmarkForm.svelte';
	import {
		BenchmarkSetupForm,
		type BenchmarkStart
	} from '$lib/components/benchmark-setup/setup-form.svelte';
	import type { BenchmarkDraft } from '$lib/interface-actions/actions';
	import {
		attachFailure,
		benchmarkStart,
		benchmarkStartFromDraft,
		latestBenchmarkJobIds
	} from '$lib/workspace/setup-start';
	import { draftIn } from '$lib/workspace/study-area';
	import SetupPanel from './SetupPanel.svelte';

	interface Props {
		/** The workspace's runs; the newest benchmark among them is where the form starts. */
		jobs: WorkspaceJob[];
		/** A setup something supplied; it takes the place of the last benchmark's. */
		draft?: BenchmarkDraft;
		/** Where the workspace works: where a first benchmark here starts. */
		area?: FocusAreaValue | null;
		/** The benchmark's jobs have been submitted; resolves once they are in the workspace. */
		onStarted: (jobIds: string[]) => Promise<void>;
		onCancel: () => void;
	}

	let { jobs, draft, area = null, onStarted, onCancel }: Props = $props();

	const store = new BenchmarkStore();
	const form = new BenchmarkSetupForm(
		() => store,
		(runId) => submitted(runId)
	);
	onDestroy(() => store.stopPolling());

	let regions = $state<Region[]>([]);
	let datasets = $state<Dataset[]>([]);
	let parameterDefaults = $state<RompDefaults | null>(null);
	let guardrails = $state<GuardrailThresholds | null>(null);
	let startFrom = $state<BenchmarkStart | null>(null);
	let dataLoaded = $state(false);
	let error = $state<string | null>(null);

	const note = $derived(
		draft
			? 'Filled in by the assistant. Check it over and change anything before you run it.'
			: startFrom
				? 'Starts from this workspace’s most recent benchmark: its region, observations, models, area of interest and onset definition. Change anything before you run it.'
				: null
	);

	/** The last benchmark's setup; a workspace without one, or one that cannot be read, starts blank. */
	async function previousSetup(jobIds: string[]): Promise<BenchmarkStart | null> {
		const previous = await Promise.allSettled(jobIds.map((id) => getJob(id)));
		return benchmarkStart(
			previous.flatMap((job) => (job.status === 'fulfilled' ? [job.value] : []))
		);
	}

	async function loadChoices(jobIds: string[]) {
		const [fetchedRegions, fetchedDatasets, fetchedDefaults, fetchedGuardrails, previous] =
			await Promise.allSettled([
				getRegions(),
				getDatasets(),
				getRompDefaults(),
				getGuardrailThresholds(),
				previousSetup(jobIds)
			]);
		regions = settled(fetchedRegions, []);
		datasets = settled(fetchedDatasets, []);
		parameterDefaults = settled(fetchedDefaults, null);
		guardrails = settled(fetchedGuardrails, null);
		const inWorkspaceArea = draftIn(area);
		startFrom = draft
			? benchmarkStartFromDraft(draft, datasets)
			: (settled(previous, null) ??
				(inWorkspaceArea ? benchmarkStartFromDraft(inWorkspaceArea, datasets) : null));
		dataLoaded = true;
	}

	// Once, for the workspace as it was when the form opened.
	let loading = false;
	$effect(() => {
		if (loading) return;
		loading = true;
		void loadChoices(latestBenchmarkJobIds(jobs));
	});

	async function submitted(runId: string) {
		const started = store.jobs.filter((job) => job.run_id === runId).map((job) => job.id);
		try {
			await onStarted(started);
		} catch {
			error = attachFailure('benchmark');
		}
	}
</script>

<SetupPanel title="New benchmark" {note} {error} {onCancel}>
	<BenchmarkForm
		{form}
		{regions}
		{datasets}
		{dataLoaded}
		{parameterDefaults}
		{guardrails}
		{startFrom}
	/>
</SetupPanel>
