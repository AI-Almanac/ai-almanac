<script lang="ts">
	/**
	 * Where the blend beats climatology, per grid point.
	 *
	 * The pooled table above says whether the blend wins on average; this says
	 * where. It reads the blend's per-grid-point summary, which nothing consumed
	 * before, and colours each point by its skill against the same unconditional
	 * climatology baseline the table uses, so a number means the same thing in both.
	 */
	import { onDestroy, onMount } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { BASEMAP_STYLES, isDarkBasemap, type BasemapStyleId } from '$lib/basemaps';
	import { getBlendCellMetrics, type BlendCellGrid, type BlendCellMetrics } from '$lib/api';
	import SegmentedTabs, { type SegmentedTabOption } from '$lib/components/SegmentedTabs.svelte';
	import { interpolateStops } from '$lib/components/metric-map/gridData';
	import { formatSkillValue } from '$lib/skill-series';
	import {
		SKILL_STOPS,
		buildSkillCells,
		shareBeatingBaseline,
		skillBounds
	} from './blend-skill-map';

	type Props = { jobId: string };
	let { jobId }: Props = $props();

	const SOURCE = 'blend-skill';
	const FILL_LAYER = 'blend-skill-fill';
	const LINE_LAYER = 'blend-skill-outline';

	let mapHost = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);
	let resizeObserver: ResizeObserver | null = null;

	let basemap = $state<BasemapStyleId>('carto-light');
	let metrics = $state<BlendCellMetrics | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let requested = $state<string | null>(null);

	let hover = $state<{
		skill: number;
		observations: number | null;
		lat: number;
		lon: number;
		clipped: boolean;
		x: number;
		y: number;
	} | null>(null);

	const grids = $derived(metrics?.grids ?? []);
	const grid = $derived<BlendCellGrid | null>(
		grids.find((g) => g.metric === requested) ?? grids[0] ?? null
	);
	const options = $derived<SegmentedTabOption[]>(
		grids.map((g) => ({ value: g.metric, label: g.label }))
	);
	const share = $derived(grid ? shareBeatingBaseline(grid) : null);
	const extent = $derived(grid?.scale_max_abs ?? 0);
	const lowCountPoints = $derived.by(() => {
		if (!grid || !metrics) return 0;
		let count = 0;
		for (let i = 0; i < grid.counts.length; i++) {
			for (let j = 0; j < grid.counts[i].length; j++) {
				const n = grid.counts[i][j];
				if (grid.values[i]?.[j] != null && n != null && n < metrics.min_observations) count += 1;
			}
		}
		return count;
	});

	function basemapUrl(): string {
		return (BASEMAP_STYLES.find((s) => s.id === basemap) ?? BASEMAP_STYLES[0]).url;
	}

	const outlineColor = $derived(
		isDarkBasemap(basemap) ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.2)'
	);

	function renderCells(fit: boolean) {
		if (!map || !mapReady || !grid || !metrics) return;
		const geojson = buildSkillCells(grid, {
			minObservations: metrics.min_observations,
			cellSizeDeg: metrics.cell_size_deg
		});
		const existing = map.getSource(SOURCE) as maplibregl.GeoJSONSource | undefined;
		if (existing) {
			existing.setData(geojson);
		} else {
			map.addSource(SOURCE, { type: 'geojson', data: geojson });
			map.addLayer({
				id: FILL_LAYER,
				type: 'fill',
				source: SOURCE,
				paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'opacity'] }
			});
			map.addLayer({
				id: LINE_LAYER,
				type: 'line',
				source: SOURCE,
				paint: {
					'line-color': outlineColor,
					// Cell borders would swamp the fills when zoomed out.
					'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0, 8, 0.6]
				}
			});
		}
		if (fit) {
			const bounds = skillBounds(grid, metrics.cell_size_deg);
			if (bounds) map.fitBounds(bounds, { padding: 24, duration: 0 });
		}
	}

	onMount(async () => {
		try {
			metrics = await getBlendCellMetrics(jobId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load per-point skill.';
		} finally {
			loading = false;
		}

		if (!mapHost || grids.length === 0) return;
		map = new maplibregl.Map({
			container: mapHost,
			style: basemapUrl(),
			center: [20, 10],
			zoom: 1.5,
			attributionControl: false
		});
		map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
		map.on('load', () => {
			mapReady = true;
			renderCells(true);
		});
		// The panel column changes width as the chat rail collapses.
		resizeObserver = new ResizeObserver(() => map?.resize());
		resizeObserver.observe(mapHost);

		map.on('mousemove', FILL_LAYER, (event: maplibregl.MapLayerMouseEvent) => {
			const feature = event.features?.[0];
			if (!feature) return;
			const p = feature.properties as {
				skill: number;
				observations: number | null;
				lat: number;
				lon: number;
				clipped: boolean;
			};
			hover = {
				skill: Number(p.skill),
				observations: p.observations == null ? null : Number(p.observations),
				lat: Number(p.lat),
				lon: Number(p.lon),
				// maplibre serializes feature properties, so booleans arrive as strings.
				clipped: String(p.clipped) === 'true',
				x: event.point.x + 14,
				y: event.point.y - 8
			};
		});
		map.on('mouseleave', FILL_LAYER, () => (hover = null));
	});

	// Re-render on metric change, and re-fit only when the grid's footprint could
	// differ — every metric here shares one grid, so fitting once is enough.
	$effect(() => {
		void grid;
		renderCells(false);
	});

	// Only restyle on an actual basemap change. Without the applied-value guard this
	// also fires when the map first becomes ready, tearing down and re-adding the
	// cell layers for the style they were already built against.
	let appliedBasemap: BasemapStyleId | null = null;
	$effect(() => {
		const next = basemap;
		if (!map || !mapReady) return;
		if (appliedBasemap === null) {
			appliedBasemap = next;
			return;
		}
		if (appliedBasemap === next) return;
		appliedBasemap = next;
		map.setStyle(basemapUrl());
		map.once('styledata', () => renderCells(false));
	});

	onDestroy(() => {
		resizeObserver?.disconnect();
		map?.remove();
		map = null;
	});

	const legendStops = [0, 0.25, 0.5, 0.75, 1].map((t) => interpolateStops(SKILL_STOPS, t));
</script>

<section class="skill-map" aria-label="Blend skill by grid point">
	<div class="map-topline">
		<h3>By grid point</h3>
		{#if options.length > 1}
			<SegmentedTabs
				{options}
				value={grid?.metric ?? ''}
				onSelect={(value) => (requested = value)}
				ariaLabel="Map metric"
			/>
		{/if}
	</div>

	{#if loading}
		<p class="muted">Loading per-point skill…</p>
	{:else if error}
		<p class="muted">{error}</p>
	{:else if grids.length === 0}
		<p class="muted">
			This blend has no per-grid-point summary, so its skill can only be read pooled over the
			region.
		</p>
	{:else}
		<div class="map-frame">
			<div class="map-host" bind:this={mapHost}></div>
			{#if hover}
				<div class="map-tooltip" style={`left: ${hover.x}px; top: ${hover.y}px`} role="tooltip">
					<strong>{formatSkillValue(hover.skill)}</strong>
					<span>{hover.lat.toFixed(2)}, {hover.lon.toFixed(2)}</span>
					{#if hover.observations != null}
						<span>{hover.observations} point-years</span>
					{/if}
					{#if hover.clipped}
						<span class="flag">beyond the scale</span>
					{/if}
				</div>
			{/if}
		</div>

		<div class="legend">
			<span>worse</span>
			<span class="ramp" style={`background: linear-gradient(to right, ${legendStops.join(', ')})`}
			></span>
			<span>better</span>
			<span class="ticks">
				−{formatSkillValue(extent)} · 0 · +{formatSkillValue(extent)}
			</span>
		</div>

		<label class="basemap">
			Basemap
			<select bind:value={basemap}>
				{#each BASEMAP_STYLES as style (style.id)}
					<option value={style.id}>{style.label}</option>
				{/each}
			</select>
		</label>

		<p class="caption">
			{#if share && share.total > 0}
				{grid?.label} against Climatology (unconditional) at each grid point. The blend beats it at
				<strong>{share.better} of {share.total}</strong>
				points ({Math.round((100 * share.better) / share.total)}%).
			{/if}
			{#if grid && grid.clipped > 0 && grid.value_min != null && grid.value_max != null}
				Skill is a ratio, so points where climatology scored near zero run far past the rest — the
				scale stops at ±{formatSkillValue(extent)} and {grid.clipped}
				{grid.clipped === 1 ? 'point' : 'points'} sit beyond it, as far as {formatSkillValue(
					Math.abs(grid.value_min) > Math.abs(grid.value_max) ? grid.value_min : grid.value_max
				)}.
			{/if}
			{#if lowCountPoints > 0 && metrics}
				Points scored on fewer than {metrics.min_observations} point-years are faded; {lowCountPoints}
				{lowCountPoints === 1 ? 'is' : 'are'} below that.
			{/if}
		</p>
	{/if}
</section>

<style>
	.skill-map {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.map-topline {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
	}

	.map-topline h3 {
		margin: 0;
		font-size: 0.72rem;
		font-weight: 800;
		color: var(--color-text);
	}

	.map-frame {
		position: relative;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		overflow: hidden;
	}

	/* Aspect ratio rather than a fixed height, so the map scales with the column. */
	.map-host {
		width: 100%;
		aspect-ratio: 4 / 3;
	}

	.map-tooltip {
		position: absolute;
		z-index: 2;
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		padding: 0.35rem 0.5rem;
		border-radius: 0.35rem;
		background: rgba(20, 26, 30, 0.92);
		color: #f4f4f4;
		font-family: var(--font-mono);
		font-size: 0.68rem;
		pointer-events: none;
		white-space: nowrap;
	}

	.map-tooltip .flag {
		color: #f4a582;
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.45rem;
		color: var(--color-text-muted);
		font-size: 0.68rem;
	}

	.ramp {
		flex: 1 1 8rem;
		height: 0.5rem;
		border: 1px solid var(--color-border-subtle);
		border-radius: 999rem;
	}

	.ticks {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}

	.basemap {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--color-text-muted);
		font-size: 0.68rem;
	}

	.basemap select {
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		background: var(--color-surface);
		color: var(--color-text);
		font-family: var(--font-body);
		font-size: 0.68rem;
		padding: 0.15rem 0.3rem;
	}

	.caption {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.7rem;
		line-height: 1.45;
	}

	.muted {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.72rem;
	}
</style>
