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

	$effect(() => {
		if (!mapReady) return;
		setData(REGION, regionShape ?? EMPTY);
		if (!value) frame();
	});
</script>

<div class="focus-area">
	<div class="map" bind:this={container}></div>
	<div class="controls">
		<button type="button" onclick={drawing ? stopDrawing : startDrawing}>
			{drawing ? 'Cancel' : value ? 'Redraw box' : 'Draw box'}
		</button>
		{#if value}
			<button type="button" onclick={() => onchange(null)}>Clear</button>
			<small>
				{value.lat_min}° to {value.lat_max}° lat, {value.lon_min}° to {value.lon_max}° lon
			</small>
		{:else}
			<small>{drawing ? 'Click and drag on the map.' : 'Whole region'}</small>
		{/if}
	</div>
</div>

<style>
	.focus-area {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.map {
		inline-size: 100%;
		aspect-ratio: 16 / 9;
		min-block-size: 12rem;
		border-radius: 0.375rem;
		overflow: hidden;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}
</style>
