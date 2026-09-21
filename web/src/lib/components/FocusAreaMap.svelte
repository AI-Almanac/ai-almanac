<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import '$lib/maplibre-worker';
	import type { FeatureCollection, Position } from 'geojson';
	import { BASEMAP_STYLES } from '$lib/basemaps';
	import type { BboxExtent } from '$lib/api/jobs';
	import { getRegionBoundary } from '$lib/api/regions';

	interface Props {
		value: BboxExtent | null;
		/** Bounding box to frame when nothing is drawn yet. */
		extent?: BboxExtent | null;
		/** Region whose border is highlighted so the user sees what a box carves out of. */
		regionId?: string | null;
		onchange: (value: BboxExtent | null) => void;
	}

	const { value, extent = null, regionId = null, onchange }: Props = $props();

	const SOURCE = 'focus-area';
	const REGION = 'focus-region';
	const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
	let container = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);
	let drawing = $state(false);
	let anchor: maplibregl.LngLat | null = null;
	let regionShape = $state<FeatureCollection | null>(null);

	function boxFrom(a: maplibregl.LngLat, b: maplibregl.LngLat): BboxExtent {
		const round = (n: number) => Math.round(n * 100) / 100;
		return {
			lat_min: round(Math.min(a.lat, b.lat)),
			lat_max: round(Math.max(a.lat, b.lat)),
			lon_min: round(Math.min(a.lng, b.lng)),
			lon_max: round(Math.max(a.lng, b.lng))
		};
	}

	function polygon(box: BboxExtent | null): FeatureCollection {
		if (!box) return EMPTY;
		const { lat_min, lat_max, lon_min, lon_max } = box;
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
		let box: BboxExtent | null = null;
		const visit = (p: Position) => {
			box = box
				? {
						lat_min: Math.min(box.lat_min, p[1]),
						lat_max: Math.max(box.lat_max, p[1]),
						lon_min: Math.min(box.lon_min, p[0]),
						lon_max: Math.max(box.lon_max, p[0])
					}
				: { lat_min: p[1], lat_max: p[1], lon_min: p[0], lon_max: p[0] };
		};
		for (const { geometry } of shape.features) {
			if (geometry.type === 'Polygon') geometry.coordinates.flat().forEach(visit);
			else if (geometry.type === 'MultiPolygon') geometry.coordinates.flat(2).forEach(visit);
		}
		return box;
	}

	function setData(source: string, data: FeatureCollection) {
		(map?.getSource(source) as maplibregl.GeoJSONSource | undefined)?.setData(data);
	}

	function fitTo(box: BboxExtent | null) {
		if (!box || !map) return;
		map.fitBounds(
			[
				[box.lon_min, box.lat_min],
				[box.lon_max, box.lat_max]
			],
			{ padding: 24, duration: 0 }
		);
	}

	function frame() {
		fitTo(value ?? extent ?? (regionShape ? shapeExtent(regionShape) : null));
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
			// Region first so the box always paints above it.
			map?.addSource(REGION, { type: 'geojson', data: regionShape ?? EMPTY });
			map?.addLayer({
				id: `${REGION}-fill`,
				type: 'fill',
				source: REGION,
				paint: { 'fill-color': '#0f766e', 'fill-opacity': 0.12 }
			});
			map?.addLayer({
				id: `${REGION}-line`,
				type: 'line',
				source: REGION,
				paint: { 'line-color': '#0f766e', 'line-width': 1.5, 'line-opacity': 0.8 }
			});
			map?.addSource(SOURCE, { type: 'geojson', data: polygon(value) });
			map?.addLayer({
				id: `${SOURCE}-fill`,
				type: 'fill',
				source: SOURCE,
				paint: { 'fill-color': '#2f6fed', 'fill-opacity': 0.2 }
			});
			map?.addLayer({
				id: `${SOURCE}-line`,
				type: 'line',
				source: SOURCE,
				paint: { 'line-color': '#2f6fed', 'line-width': 2 }
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
			const box = boxFrom(anchor, e.lngLat);
			stopDrawing();
			if (box.lat_min === box.lat_max || box.lon_min === box.lon_max) {
				setData(SOURCE, polygon(value));
				return;
			}
			onchange(box);
		});
	});

	onDestroy(() => map?.remove());

	// The region's border, when the boundary service knows it. A miss just leaves
	// the basemap, so the map stays usable for regions without boundary data.
	$effect(() => {
		const id = regionId;
		regionShape = null;
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

	function isFeatureCollection(value: unknown): value is FeatureCollection {
		return (
			typeof value === 'object' &&
			value != null &&
			(value as { type?: unknown }).type === 'FeatureCollection' &&
			Array.isArray((value as { features?: unknown }).features)
		);
	}

	$effect(() => {
		if (mapReady) setData(SOURCE, polygon(value));
	});

	// The region highlight means "this is what gets scored", so it yields to the
	// box once one exists and returns when the box is cleared.
	$effect(() => {
		if (!mapReady) return;
		setData(REGION, value ? EMPTY : (regionShape ?? EMPTY));
		if (!value) frame();
	});
</script>

<div class="focus-area">
	<div class="map-frame">
		<div class="map" bind:this={container}></div>
		{#if drawing}
			<p class="drawing-hint">Click and drag to draw the box</p>
		{/if}
	</div>
	<div class="controls">
		<button
			type="button"
			class="draw"
			class:cancel={drawing}
			title={value ? 'Replace the current box with a new one' : 'Limit scoring to a box you draw'}
			onclick={drawing ? stopDrawing : startDrawing}
		>
			{drawing ? 'Cancel' : value ? 'Redraw box' : 'Draw box'}
		</button>
		{#if value}
			<button
				type="button"
				class="clear"
				title="Remove the box and score the whole region"
				onclick={() => onchange(null)}
			>
				Clear
			</button>
			<small>
				{value.lat_min}° to {value.lat_max}° lat, {value.lon_min}° to {value.lon_max}° lon
			</small>
		{:else if !drawing}
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

	.drawing-hint {
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

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
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
