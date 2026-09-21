<script lang="ts">
	/**
	 * Where the blend beats climatology, per grid point or administrative area.
	 *
	 * The pooled table above says whether the blend wins on average; this says
	 * where. It reads the blend's per-grid-point summary, which nothing consumed
	 * before, and colors each point by its skill against the same Traditional
	 * Climatology baseline the table uses, so a number means the same thing in both.
	 */
	import { untrack } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import '$lib/maplibre-worker';
	import { BASEMAP_STYLES, isDarkBasemap, type BasemapStyleId } from '$lib/basemaps';
	import { getBlendCellMetrics, type BlendCellMetrics, type SkillLayer } from '$lib/api';
	import { getRegionBoundary } from '$lib/api/regions';
	import SegmentedTabs, { type SegmentedTabOption } from '$lib/components/SegmentedTabs.svelte';
	import { interpolateStops } from '$lib/components/metric-map/gridData';
	import { formatSkillValue } from '$lib/skill-series';
	import {
		SKILL_STOPS,
		buildAreaSkillCells,
		buildSkillCells,
		featureBounds,
		isAreaMetric,
		lowCountPoints as countLowPoints,
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
	let appliedBasemap: BasemapStyleId | null = null;
	/** The blend the camera is currently framed on; null means it needs fitting. */
	let fittedJob: string | null = null;

	// Dark, as the benchmark and forecast maps are: the diverging scale's midpoint is
	// near-white, which reads as neutral against dark but disappears against light.
	let basemap = $state<BasemapStyleId>('carto-dark');
	let metrics = $state<BlendCellMetrics | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let requested = $state<string | null>(null);
	/** ADM3 outlines for named areas; null draws them as centroid squares instead. */
	let boundaries = $state<GeoJSON.FeatureCollection | null>(null);
	/** The basemap style never arrived; the frame would otherwise be silently blank. */
	let stalled = $state(false);
	let styleTimer: ReturnType<typeof setTimeout> | null = null;

	let hover = $state<{
		skill: number;
		observations: number | null;
		lat: number;
		lon: number;
		name: string | null;
		clipped: boolean;
		x: number;
		y: number;
	} | null>(null);

	const grids = $derived<SkillLayer[]>(
		metrics ? (metrics.grids.length ? metrics.grids : metrics.areas) : []
	);
	const grid = $derived<SkillLayer | null>(
		grids.find((g) => g.metric === requested) ?? grids[0] ?? null
	);
	const options = $derived<SegmentedTabOption[]>(
		grids.map((g) => ({ value: g.metric, label: g.label }))
	);
	const share = $derived(grid ? shareBeatingBaseline(grid) : null);
	const extent = $derived(grid?.scale_max_abs ?? 0);
	const lowCountPoints = $derived(
		grid && metrics ? countLowPoints(grid, metrics.min_observations) : 0
	);
	const byArea = $derived(grid != null && isAreaMetric(grid));
	const unitWord = $derived(byArea ? 'areas' : 'points');

	function basemapUrl(): string {
		return (BASEMAP_STYLES.find((s) => s.id === basemap) ?? BASEMAP_STYLES[0]).url;
	}

	const outlineColor = $derived(
		isDarkBasemap(basemap) ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.2)'
	);

	function renderCells(fit: boolean) {
		if (!map || !mapReady || !grid || !metrics) return;
		const geojson = isAreaMetric(grid)
			? buildAreaSkillCells(grid, boundaries, { minObservations: metrics.min_observations })
			: buildSkillCells(grid, {
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
			const bounds = isAreaMetric(grid)
				? featureBounds(geojson)
				: skillBounds(grid, metrics.cell_size_deg);
			if (bounds) map.fitBounds(bounds, { padding: 24, duration: 0 });
		}
	}

	// Reload when the selected blend changes; the component is reused across blends.
	$effect(() => {
		const id = jobId;
		let cancelled = false;
		loading = true;
		error = null;
		metrics = null;
		requested = null;
		getBlendCellMetrics(id)
			.then((result) => {
				if (cancelled) return;
				metrics = result;
				boundaries = null;
				if (result.areas.length && result.region_id) void loadBoundaries(result.region_id);
			})
			.catch((e) => {
				if (!cancelled) {
					error = e instanceof Error ? e.message : 'Could not load per-point skill.';
				}
			})
			.finally(() => {
				if (!cancelled) loading = false;
			});
		return () => {
			cancelled = true;
		};
	});
	/** Outlines for named areas. A failure leaves centroid squares rather than an error. */
	async function loadBoundaries(regionId: string) {
		try {
			const { geojson } = await getRegionBoundary(regionId, 'adm3');
			if (isFeatureCollection(geojson)) boundaries = geojson;
		} catch {
			boundaries = null;
		}
	}
	function isFeatureCollection(value: unknown): value is GeoJSON.FeatureCollection {
		return (
			typeof value === 'object' &&
			value != null &&
			(value as { type?: unknown }).type === 'FeatureCollection' &&
			Array.isArray((value as { features?: unknown }).features)
		);
	}

	/**
	 * Build the map when its host element appears, not on mount.
	 *
	 * The host sits behind an {#if} that is false while the fetch is in flight, so
	 * during onMount it does not exist yet — constructing the map there leaves a
	 * blank frame forever, because nothing retries once the element arrives.
	 *
	 * basemapUrl() is read untracked: this effect must depend on the host alone, or
	 * changing the basemap would tear the whole map down and rebuild it.
	 */
	$effect(() => {
		const host = mapHost;
		if (!host) return;

		const instance = new maplibregl.Map({
			container: host,
			style: untrack(() => basemapUrl()),
			center: [20, 10],
			zoom: 1.5,
			attributionControl: false
		});
		map = instance;
		appliedBasemap = untrack(() => basemap);
		// A new map opens at the default camera, so it must refit even for a blend
		// that an earlier instance had already framed.
		fittedJob = null;
		instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
		// The basemaps are CARTO/OSM, whose licences require the credit.
		instance.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
		/**
		 * 'style.load', not 'load': the cells are local data and must not wait on the
		 * basemap. 'load' only fires once the whole first render completes, including
		 * the basemap's sprites and glyphs, so one stalled third-party request leaves
		 * the frame blank with no error — indistinguishable from a blend with no
		 * points. 'style.load' is the documented point at which sources and layers
		 * can be added, and it depends only on the style JSON.
		 *
		 * Only the flag is set here; the redraw effect below owns every render.
		 */
		instance.on('style.load', () => (mapReady = true));
		// A style that never arrives leaves the same empty frame, so name it.
		instance.on('error', (event: { error?: { message?: string } }) => {
			if (!mapReady) error = event.error?.message ?? 'The map could not be loaded.';
		});
		// Nothing above guarantees an error on a request that merely hangs.
		styleTimer = setTimeout(() => {
			if (!mapReady && !error) stalled = true;
		}, 10_000);

		instance.on('mousemove', FILL_LAYER, (event: maplibregl.MapLayerMouseEvent) => {
			const feature = event.features?.[0];
			if (!feature) return;
			const p = feature.properties as {
				skill: number;
				observations: number | null;
				lat: number;
				lon: number;
				name: string | null;
				clipped: boolean;
			};
			hover = {
				skill: Number(p.skill),
				observations: p.observations == null ? null : Number(p.observations),
				lat: Number(p.lat),
				lon: Number(p.lon),
				name: p.name == null || p.name === 'null' ? null : String(p.name),
				// maplibre serializes feature properties, so booleans arrive as strings.
				clipped: String(p.clipped) === 'true',
				x: event.point.x + 14,
				y: event.point.y - 8
			};
		});
		instance.on('mouseleave', FILL_LAYER, () => (hover = null));

		// The panel column changes width as the chat rail collapses.
		const observer = new ResizeObserver(() => instance.resize());
		observer.observe(host);

		return () => {
			if (styleTimer) clearTimeout(styleTimer);
			styleTimer = null;
			observer.disconnect();
			instance.remove();
			if (map === instance) {
				map = null;
				mapReady = false;
				stalled = false;
			}
		};
	});

	// Redraw on metric or blend change, refitting only when the blend changed —
	// every metric shares one grid, so switching metric must not move the camera.
	$effect(() => {
		if (!grid || !metrics || !mapReady) return;
		// Outlines arriving after the first draw reframe once, onto the polygons.
		const frame = `${metrics.job_id}:${boundaries ? 'outlines' : 'centroids'}`;
		const fit = fittedJob !== frame;
		renderCells(fit);
		if (fit) fittedJob = frame;
	});

	// Only restyle on an actual change. The map is built with the current basemap
	// already applied, so without this guard the effect's first run would tear the
	// style down and re-add the cell layers for the style they already match.
	$effect(() => {
		const next = basemap;
		if (!map || !mapReady || appliedBasemap === next) return;
		appliedBasemap = next;
		map.setStyle(basemapUrl());
		// setStyle discards the cell layers, so re-add them once the new style parses.
		map.once('style.load', () => renderCells(false));
	});

	const legendStops = [0, 0.25, 0.5, 0.75, 1].map((t) => interpolateStops(SKILL_STOPS, t));
</script>

<section
	class="skill-map"
	aria-label={`Blend skill by ${byArea ? 'area' : 'grid point'}`}
	data-tour="blend-maps"
>
	<div class="map-topline">
		<h3>{byArea ? 'By area' : 'By grid point'}</h3>
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
			This blend has no per-point summary, so its skill can only be read pooled over the region.
		</p>
	{:else}
		<div class="map-frame">
			<div class="map-host" bind:this={mapHost}></div>
			{#if stalled}
				<p class="map-stalled">
					The basemap did not load, so the grid cannot be drawn. Check the browser console for a
					blocked request to <code>basemaps.cartocdn.com</code>.
				</p>
			{/if}
			{#if hover}
				<div class="map-tooltip" style={`left: ${hover.x}px; top: ${hover.y}px`} role="tooltip">
					<strong>{formatSkillValue(hover.skill)}</strong>
					<span>{hover.name ?? `${hover.lat.toFixed(2)}, ${hover.lon.toFixed(2)}`}</span>
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
				{grid?.label} against Traditional Climatology at each {byArea ? 'area' : 'grid point'}. The
				blend beats it at
				<strong>{share.better} of {share.total}</strong>
				{unitWord} ({Math.round((100 * share.better) / share.total)}%).
			{/if}
			{#if grid && grid.clipped > 0 && grid.value_min != null && grid.value_max != null}
				Skill is a ratio, so points where Traditional Climatology scored near zero run far past the
				rest — the scale stops at ±{formatSkillValue(extent)} and {grid.clipped}
				{grid.clipped === 1 ? 'point' : 'points'} sit beyond it, as far as {formatSkillValue(
					Math.abs(grid.value_min) > Math.abs(grid.value_max) ? grid.value_min : grid.value_max
				)}.
			{/if}
			{#if lowCountPoints > 0 && metrics}
				{byArea ? 'Areas' : 'Points'} scored on fewer than {metrics.min_observations} point-years are
				faded; {lowCountPoints}
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

	/* A definite height, as the benchmark and forecast maps use: MapLibre measures
	   its container on construction, and an aspect-ratio box leaves the canvas
	   unsized, so the controls appear over an empty frame. clamp keeps it
	   responsive rather than pinning a pixel height. */
	.map-frame {
		position: relative;
		height: clamp(18rem, 45vh, 27rem);
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		overflow: hidden;
	}

	.map-host {
		position: absolute;
		inset: 0;
	}

	/* MapLibre sizes its own canvas from inline styles that do not survive this
	   layout; both other maps in the app carry the same override. */
	.map-frame :global(.maplibregl-canvas-container),
	.map-frame :global(.maplibregl-canvas) {
		width: 100% !important;
		height: 100% !important;
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

	.map-stalled {
		position: absolute;
		inset: auto 0.75rem 0.75rem 0.75rem;
		margin: 0;
		padding: 0.5rem 0.6rem;
		border-radius: 0.35rem;
		background: var(--color-surface);
		border: 1px solid var(--color-danger-border);
		color: var(--color-text);
		font-size: 0.7rem;
		line-height: 1.45;
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
