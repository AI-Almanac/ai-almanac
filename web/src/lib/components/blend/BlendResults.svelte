<script lang="ts">
	import { getBlendSummary } from '$lib/api';
	import BlendSkillPanel from './BlendSkillPanel.svelte';
	import type { AreaExportContext } from '$lib/components/metric-map/exportTitle';
	import type { MapInitialView } from '$lib/components/metric-map/initialView';
	import { parsePooledSummary, type SkillRow } from './blend-summary';

	interface Props {
		jobId: string;
		/** The pooled summary is published a few seconds after the blend completes. */
		summaryReady: boolean;
		/** Changes when the blend's results grow, so the summary and map reload. */
		revision?: string;
		/** How the skill map opens; left out, as it always has. */
		mapView?: MapInitialView;
		/** Offers "Export map" on the skill map, naming the areas the blend was limited to. */
		mapExport?: AreaExportContext;
	}

	let { jobId, summaryReady, revision = '', mapView, mapExport }: Props = $props();

	let skill = $state<SkillRow[]>([]);

	// The CSV is read by the backend, never fetched from the bucket by the browser.
	$effect(() => {
		if (!summaryReady) {
			skill = [];
			return;
		}
		void revision;
		const id = jobId;
		let cancelled = false;
		getBlendSummary(id)
			.then((text) => {
				if (!cancelled) skill = parsePooledSummary(text);
			})
			.catch(() => {
				if (!cancelled) skill = [];
			});
		return () => {
			cancelled = true;
		};
	});
</script>

{#if skill.length > 0}
	<div class="skill">
		<h2>Forecast skill</h2>
		<BlendSkillPanel series={skill} {jobId} {revision} {mapView} {mapExport} />
	</div>
{/if}
