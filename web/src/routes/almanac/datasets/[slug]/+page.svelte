<script lang="ts">
	import AlmanacDetail, { value } from '$lib/almanac/AlmanacDetail.svelte';
	import TagList from '$lib/almanac/TagList.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let dataset = $derived(data.dataset);
</script>

<AlmanacDetail
	backHref="/almanac#datasets"
	backLabel="Back to datasets"
	eyebrow={dataset.role}
	title={dataset.name}
	summary={dataset.summary}
	notes={dataset.notes}
	references={dataset.references}
>
	{#snippet sections()}
		<section aria-labelledby="source-title">
			<h2 id="source-title">Source</h2>
			<dl>
				<div>
					<dt>Provider</dt>
					<dd>{value(dataset.source)}</dd>
				</div>
				<div>
					<dt>Coverage</dt>
					<dd>{value(dataset.coverage)}</dd>
				</div>
				<div>
					<dt>Resolution</dt>
					<dd>{value(dataset.resolution)}</dd>
				</div>
			</dl>
		</section>

		<section aria-labelledby="variables-title">
			<h2 id="variables-title">Variables</h2>
			<TagList items={dataset.variables} />
		</section>

		<section aria-labelledby="use-title">
			<h2 id="use-title">Used For</h2>
			<TagList items={dataset.usedFor} />
		</section>
	{/snippet}
</AlmanacDetail>
