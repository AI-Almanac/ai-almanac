<!--
	Stands in for the benchmark form: says what it was started from, and reports a
	submitted run through the setup form, as the real form does.
-->
<script lang="ts">
	import type { Job } from '../../src/lib/api';
	import type {
		BenchmarkSetupForm,
		BenchmarkStart
	} from '../../src/lib/components/benchmark-setup/setup-form.svelte';

	interface Props {
		form: BenchmarkSetupForm;
		startFrom: BenchmarkStart | null;
		dataLoaded: boolean;
	}

	let { form, startFrom, dataLoaded }: Props = $props();

	function submit() {
		const jobs = [
			{ id: 'new-aifs', run_id: 'run-new' },
			{ id: 'new-fuxi', run_id: 'run-new' },
			{ id: 'elsewhere', run_id: 'run-other' }
		] as Job[];
		form.handleBenchmarkSubmitted('run-new', jobs, null);
	}
</script>

<p data-testid="form-start">
	{dataLoaded ? JSON.stringify(startFrom) : 'loading'}
</p>
<button type="button" onclick={submit}>Run benchmark</button>
