<script lang="ts">
	import AlmanacDetail from '$lib/almanac/AlmanacDetail.svelte';
	import TagList from '$lib/almanac/TagList.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let architecture = $derived(data.architecture);
</script>

<AlmanacDetail
	backHref="/almanac#architectures"
	backLabel="Back to architectures"
	eyebrow="Architecture"
	title={architecture.name}
	summary={architecture.summary}
	notes={architecture.notes}
	references={architecture.references}
>
	{#snippet sections()}
		<section aria-labelledby="families-title">
			<h2 id="families-title">Model Families</h2>
			<TagList items={architecture.modelFamilies} />
		</section>

		<section aria-labelledby="ideas-title">
			<h2 id="ideas-title">Key Ideas</h2>
			<TagList items={architecture.keyIdeas} />
		</section>
	{/snippet}
</AlmanacDetail>
