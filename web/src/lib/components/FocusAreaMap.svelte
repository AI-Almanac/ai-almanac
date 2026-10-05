<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import '$lib/maplibre-worker';
	import type { FeatureCollection, Position } from 'geojson';
	import { BASEMAP_STYLES } from '$lib/basemaps';
	import { isFocusUnits, type BboxExtent, type FocusAreaValue } from '$lib/api/jobs';
	import { getRegionBoundary } from '$lib/api/regions';

	interface Props {
		value: FocusAreaValue | null;
		/** Bounding box to frame when nothing is drawn yet. */
		extent?: BboxExtent | null;
		/** Region whose border is highlighted and whose areas can be picked. */
		regionId?: string | null;
		onchange: (value: FocusAreaValue | null) => void;
	}

	const { value, extent = null, regionId = null, onchange }: Props = $props();

	type Mode = 'region' | 'box' | 'units';
	const MODES: { id: Mode; label: string }[] = [
		{ id: 'region', label: 'Whole region' },
		{ id: 'box', label: 'Draw a box' },
		{ id: 'units', label: 'Pick areas' }
	];
	// ponytail: ADM2 only for now; a level picker slots in here once the flow is proven.
	const UNIT_LEVEL = 'adm2';

	const SOURCE = 'focus-area';
	const REGION = 'focus-region';
	const ZONES = 'focus-zones';
	const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
	let container = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);
	let drawing = $state(false);
	let anchor: maplibregl.LngLat | null = null;
	let regionShape = $state<FeatureCollection | null>(null);
	let zones = $state<FeatureCollection | null>(null);
	let zonesFailed = $state(false);
	let mode = $state<Mode>('region');

	const box = $derived(value && !isFocusUnits(value) ? value : null);
	const picked = $derived(isFocusUnits(value) ? value.units : []);

	function boxFrom(a: maplibregl.LngLat, b: maplibregl.LngLat): BboxExtent {
		const round = (n: number) => Math.round(n * 100) / 100;
		return {
			lat_min: round(Math.min(a.lat, b.lat)),
			lat_max: round(Math.max(a.lat, b.lat)),
			lon_min: round(Math.min(a.lng, b.lng)),
			lon_max: round(Math.max(a.lng, b.lng))
		};
	}

	function polygon(area: BboxExtent | null): FeatureCollection {
		if (!area) return EMPTY;
		const { lat_min, lat_max, lon_min, lon_max } = area;
		const ring = [
			[lon_min, lat_min],
			[lon_max, lat_min],
			[lon_max, lat_max],
			[lon_min, lat_max],
			[lon_min, lat_min]
		];
		return {
			type: 'FeatureCollection',
			features: [
				{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }
			]
		};
	}

	function shapeExtent(shape: FeatureCollection): BboxExtent | null {
		let bounds: BboxExtent | null = null;
		const visit = (p: Position) => {
			bounds = bounds
				? {
						lat_min: Math.min(bounds.lat_min, p[1]),
						lat_max: Math.max(bounds.lat_max, p[1]),
						lon_min: Math.min(bounds.lon_min, p[0]),
						lon_max: Math.max(bounds.lon_max, p[0])
					}
				: { lat_min: p[1], lat_max: p[1], lon_min: p[0], lon_max: p[0] };
		};
		for (const { geometry } of shape.features) {
			if (geometry.type === 'Polygon') geometry.coordinates.flat().forEach(visit);
			else if (geometry.type === 'MultiPolygon') geometry.coordinates.flat(2).forEach(visit);
		}
		return bounds;
	}

	function setData(source: string, data: FeatureCollection) {
		(map?.getSource(source) as maplibregl.GeoJSONSource | undefined)?.setData(data);
	}

	function fitTo(area: BboxExtent | null) {
		if (!area || !map) return;
		map.fitBounds(
			[
				[area.lon_min, area.lat_min],
				[area.lon_max, area.lat_max]
			],
			{ padding: 24, duration: 0 }
		);
	}

	function frame() {
		fitTo(box ?? extent ?? (regionShape ? shapeExtent(regionShape) : null));
	}

	function startDrawing() {
		drawing = true;
		map?.dragPan.disable();
		map?.getCanvas().style.setProperty('cursor', 'crosshair');
	}

	function stopDrawing() {
		drawing = false;
		anchor = null;
		map?.dragPan.enable();
		map?.getCanvas().style.removeProperty('cursor');
	}

	function setMode(next: Mode) {
		stopDrawing();
		mode = next;
		// The three choices are exclusive: leaving a mode discards its selection.
		if (next === 'region' || (next === 'box') === isFocusUnits(value)) {
			if (value) onchange(null);
		}
	}

	function zoneName(feature: maplibregl.MapGeoJSONFeature): string | null {
		const name = feature.id ?? feature.properties?.shapeName;
		return typeof name === 'string' && name ? name : null;
	}

	function togglePicked(name: string) {
		const next = picked.includes(name) ? picked.filter((n) => n !== name) : [...picked, name];
		onchange(next.length ? { level: UNIT_LEVEL, units: next } : null);
	}

	function clearSelection() {
		onchange(null);
	}

	/** Fill the viewport; the map keeps its instance and just re-measures. */
	let expanded = $state(false);
	async function setExpanded(next: boolean) {
		expanded = next;
		await tick();
		map?.resize();
		if (!value) frame();
	}

	onMount(() => {
		if (!container) return;
		map = new maplibregl.Map({
			container,
			style: BASEMAP_STYLES[0].url,
			center: [40, 9],
			zoom: 4,
			attributionControl: false
		});
		map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
		map.on('load', () => {
			if (!map) return;
			// Region first, then pickable areas, then the box, so each paints above the last.
			map.addSource(REGION, { type: 'geojson', data: regionShape ?? EMPTY });
			map.addLayer({
				id: `${REGION}-fill`,
				type: 'fill',
				source: REGION,
				paint: { 'fill-color': '#0f766e', 'fill-opacity': 0.12 }
			});
			map.addLayer({
				id: `${REGION}-line`,
				type: 'line',
				source: REGION,
				paint: { 'line-color': '#0f766e', 'line-width': 1.5, 'line-opacity': 0.8 }
			});
			// Feature state needs an id per area; the name doubles as one.
			map.addSource(ZONES, { type: 'geojson', data: zones ?? EMPTY, promoteId: 'shapeName' });
			map.addLayer({
				id: `${ZONES}-fill`,
				type: 'fill',
				source: ZONES,
				paint: {
					'fill-color': '#2f6fed',
					'fill-opacity': ['case', ['boolean', ['feature-state', 'selected'], false], 0.45, 0.04]
				}
			});
			map.addLayer({
				id: `${ZONES}-line`,
				type: 'line',
				source: ZONES,
				paint: { 'line-color': '#2f6fed', 'line-width': 0.8, 'line-opacity': 0.7 }
			});
			map.addSource(SOURCE, { type: 'geojson', data: polygon(box) });
			map.addLayer({
				id: `${SOURCE}-fill`,
				type: 'fill',
				source: SOURCE,
				paint: { 'fill-color': '#2f6fed', 'fill-opacity': 0.2 }
			});
			map.addLayer({
				id: `${SOURCE}-line`,
				type: 'line',
				source: SOURCE,
				paint: { 'line-color': '#2f6fed', 'line-width': 2 }
			});
			map.on('click', `${ZONES}-fill`, (e) => {
				if (mode !== 'units') return;
				const name = e.features?.[0] ? zoneName(e.features[0]) : null;
				if (name) togglePicked(name);
			});
			map.on('mouseenter', `${ZONES}-fill`, () => {
				if (mode === 'units') map?.getCanvas().style.setProperty('cursor', 'pointer');
			});
			map.on('mouseleave', `${ZONES}-fill`, () => {
				if (mode === 'units') map?.getCanvas().style.removeProperty('cursor');
			});
			mapReady = true;
			frame();
			// MapLibre opens the compact attribution on first render; start it folded.
			const attrib = container?.querySelector<HTMLDetailsElement>('.maplibregl-ctrl-attrib');
			attrib?.classList.remove('maplibregl-compact-show');
			attrib?.removeAttribute('open');
		});
		map.on('mousedown', (e) => {
			if (!drawing) return;
			e.preventDefault();
			anchor = e.lngLat;
		});
		map.on('mousemove', (e) => {
			if (anchor) setData(SOURCE, polygon(boxFrom(anchor, e.lngLat)));
		});
		map.on('mouseup', (e) => {
			if (!anchor) return;
			const drawn = boxFrom(anchor, e.lngLat);
			stopDrawing();
			if (drawn.lat_min === drawn.lat_max || drawn.lon_min === drawn.lon_max) {
				setData(SOURCE, polygon(box));
				return;
			}
			onchange(drawn);
		});
	});

	onDestroy(() => map?.remove());

	// A value set from outside (chat, a reloaded config) decides the mode.
	$effect(() => {
		if (isFocusUnits(value)) mode = 'units';
		else if (value) mode = 'box';
	});

	// The region's border, when the boundary service knows it. A miss just leaves
	// the basemap, so the map stays usable for regions without boundary data.
	$effect(() => {
		const id = regionId;
		regionShape = null;
		zones = null;
		zonesFailed = false;
		if (!id) return;
		let stale = false;
		getRegionBoundary(id, 'adm0')
			.then(({ geojson }) => {
				if (!stale && isFeatureCollection(geojson)) regionShape = geojson;
			})
			.catch(() => {});
		return () => {
			stale = true;
		};
	});

	// Pickable areas load on demand, the first time the mode calls for them.
	$effect(() => {
		const id = regionId;
		if (mode !== 'units' || !id || zones || zonesFailed) return;
		let stale = false;
		getRegionBoundary(id, UNIT_LEVEL)
			.then(({ geojson }) => {
				if (!stale && isFeatureCollection(geojson)) zones = geojson;
			})
			.catch(() => {
				if (!stale) zonesFailed = true;
			});
		return () => {
			stale = true;
		};
	});

	function isFeatureCollection(candidate: unknown): candidate is FeatureCollection {
		return (
			typeof candidate === 'object' &&
			candidate != null &&
			(candidate as { type?: unknown }).type === 'FeatureCollection' &&
			Array.isArray((candidate as { features?: unknown }).features)
		);
	}

	$effect(() => {
		if (mapReady) setData(SOURCE, polygon(box));
	});

	// The region highlight means "this is what gets scored", so it yields to any
	// selection and returns when the selection is cleared.
	$effect(() => {
		if (!mapReady) return;
		setData(REGION, value ? EMPTY : (regionShape ?? EMPTY));
		if (!value) frame();
	});

	$effect(() => {
		if (!mapReady || !map) return;
		setData(ZONES, zones ?? EMPTY);
		const visibility = mode === 'units' ? 'visible' : 'none';
		map.setLayoutProperty(`${ZONES}-fill`, 'visibility', visibility);
		map.setLayoutProperty(`${ZONES}-line`, 'visibility', visibility);
		map.removeFeatureState({ source: ZONES });
		for (const name of picked) map.setFeatureState({ source: ZONES, id: name }, { selected: true });
	});
</script>

<svelte:window
	onkeydown={(event) => {
		if (event.key === 'Escape' && expanded) void setExpanded(false);
	}}
/>

<div class="focus-area" class:expanded>
	<div class="toolbar">
		<div class="modes" role="group" aria-label="Area of interest">
			{#each MODES as option (option.id)}
				<button
					type="button"
					class:active={mode === option.id}
					aria-pressed={mode === option.id}
					onclick={() => setMode(option.id)}
				>
					{option.label}
				</button>
			{/each}
		</div>
		<button
			type="button"
			class="expand"
			title={expanded ? 'Back to the panel' : 'Open a larger map'}
			onclick={() => setExpanded(!expanded)}
		>
			{expanded ? 'Close' : 'Expand'}
		</button>
	</div>
	<div class="map-frame">
		<div class="map" bind:this={container}></div>
		{#if drawing}
			<p class="map-hint">Click and drag to draw the box</p>
		{:else if mode === 'units' && picked.length === 0}
			<p class="map-hint">
				{#if zonesFailed}
					Couldn't load areas.
					<button type="button" class="retry" onclick={() => (zonesFailed = false)}> Retry </button>
				{:else}
					{zones ? 'Click areas to add them' : 'Loading areas…'}
				{/if}
			</p>
		{/if}
	</div>
	<div class="controls">
		{#if mode === 'box'}
			<button
				type="button"
				class="draw"
				class:cancel={drawing}
				title={box ? 'Replace the current box with a new one' : 'Limit scoring to a box you draw'}
				onclick={drawing ? stopDrawing : startDrawing}
			>
				{drawing ? 'Cancel' : box ? 'Redraw box' : 'Draw box'}
			</button>
			{#if box}
				<button type="button" class="clear" title="Remove the box" onclick={clearSelection}>
					Clear
				</button>
				<small>
					{box.lat_min}° to {box.lat_max}° lat, {box.lon_min}° to {box.lon_max}° lon
				</small>
			{:else if !drawing}
				<small>No box yet, so the whole region is scored</small>
			{/if}
		{:else if mode === 'units'}
			{#if picked.length}
				<button type="button" class="clear" title="Deselect every area" onclick={clearSelection}>
					Clear
				</button>
				<small>{picked.length} selected: {picked.join(', ')}</small>
			{:else}
				<small>Click an area on the map to add it; click again to remove it</small>
			{/if}
		{:else}
			<small>Whole region is scored</small>
		{/if}
	</div>
</div>

<style>
	.focus-area {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.focus-area.expanded {
		position: fixed;
		inset: 0;
		z-index: 1000;
		padding: 1rem clamp(1rem, 4vw, 3rem);
		background: var(--color-surface);
	}

	.toolbar {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 0.5rem;
	}

	.expand {
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		padding: 0.3rem 0.7rem;
		background: var(--color-bg);
		color: var(--color-text);
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}

	.expanded .map-frame {
		display: flex;
		flex: 1;
		min-block-size: 0;
	}

	.expanded .map {
		aspect-ratio: auto;
		block-size: 100%;
	}

	.modes {
		display: flex;
		gap: 0.25rem;
		padding: 0.2rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
		inline-size: fit-content;
	}

	.modes button {
		border: 0;
		border-radius: 0.35rem;
		padding: 0.3rem 0.7rem;
		background: transparent;
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}

	.modes button.active {
		background: var(--color-surface-raised);
		color: var(--color-text);
		box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
	}

	.map-frame {
		position: relative;
	}

	.map {
		inline-size: 100%;
		aspect-ratio: 16 / 9;
		min-block-size: 12rem;
		border-radius: 0.375rem;
		overflow: hidden;
	}

	.map-hint {
		position: absolute;
		inset-block-start: 0.5rem;
		inset-inline: 0;
		margin: 0 auto;
		inline-size: fit-content;
		padding: 0.3rem 0.7rem;
		border-radius: 999px;
		background: var(--color-surface-raised);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		pointer-events: none;
	}

	.retry {
		padding: 0;
		border: none;
		background: none;
		color: var(--color-accent);
		font: inherit;
		text-decoration: underline;
		cursor: pointer;
		pointer-events: auto;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		min-block-size: 1.8rem;
	}

	.controls button {
		border-radius: 0.4rem;
		padding: 0.3rem 0.7rem;
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}

	.draw {
		border: 0;
		background: var(--color-accent);
		color: white;
	}

	.draw:hover {
		background: var(--color-accent-hover);
	}

	.draw.cancel,
	.clear {
		border: 1px solid var(--color-border);
		background: var(--color-bg);
		color: var(--color-text);
	}

	.controls small {
		color: var(--color-text-muted);
	}
</style>
