<script lang="ts">
	import AlmanacDetail, { value } from '$lib/almanac/AlmanacDetail.svelte';
	import TagList from '$lib/almanac/TagList.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let model = $derived(data.model);
</script>

<AlmanacDetail
	backHref="/almanac#models"
	backLabel="Back to model families"
	eyebrow={model.modelType}
	title={model.name}
	summary={model.summary}
	notes={model.notes}
	references={model.references}
>
	{#snippet sections()}
		<section class="facts" aria-labelledby="facts-title">
			<h2 id="facts-title">Model facts</h2>
			<dl>
				<div>
					<dt>Resolution</dt>
					<dd>{value(model.resolution)}</dd>
				</div>
				<div>
					<dt>Forecast range</dt>
					<dd>{value(model.forecastRange)}</dd>
				</div>
				<div>
					<dt>Cadence</dt>
					<dd>{value(model.cadence)}</dd>
				</div>
			</dl>
		</section>

		<section aria-labelledby="checkpoints-title">
			<h2 id="checkpoints-title">Checkpoints</h2>
			<TagList items={model.checkpoints} />
		</section>

		<section aria-labelledby="data-title">
			<h2 id="data-title">Data</h2>
			<div class="split-list">
				<div>
					<h3>Training</h3>
					<TagList items={model.trainingDatasets} />
				</div>
				<div>
					<h3>Validation</h3>
					<TagList items={model.validationDatasets} />
				</div>
			</div>
		</section>

		<section aria-labelledby="variables-title">
			<h2 id="variables-title">Variables</h2>
			<TagList items={model.variables} />
		</section>
	{/snippet}
</AlmanacDetail>
