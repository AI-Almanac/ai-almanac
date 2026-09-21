<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import '$lib/maplibre-worker';
	import type { FeatureCollection } from 'geojson';
	import { BASEMAP_STYLES } from '$lib/basemaps';
	import type { BboxExtent } from '$lib/api/jobs';

	interface Props {
		value: BboxExtent | null;
		extent?: BboxExtent | null;
		onchange: (value: BboxExtent | null) => void;
	}

	const { value, extent = null, onchange }: Props = $props();

	const SOURCE = 'focus-area';
	let container = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let drawing = $state(false);
	let anchor: maplibregl.LngLat | null = null;

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
		if (!box) return { type: 'FeatureCollection', features: [] };
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

	function render(box: BboxExtent | null) {
		const source = map?.getSource(SOURCE) as maplibregl.GeoJSONSource | undefined;
		source?.setData(polygon(box));
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
			fitTo(value ?? extent);
		});
		map.on('mousedown', (e) => {
			if (!drawing) return;
			e.preventDefault();
			anchor = e.lngLat;
		});
		map.on('mousemove', (e) => {
			if (anchor) render(boxFrom(anchor, e.lngLat));
		});
		map.on('mouseup', (e) => {
			if (!anchor) return;
			const box = boxFrom(anchor, e.lngLat);
			stopDrawing();
			if (box.lat_min === box.lat_max || box.lon_min === box.lon_max) {
				render(value);
				return;
			}
			onchange(box);
		});
	});

	onDestroy(() => map?.remove());

	$effect(() => render(value));
	$effect(() => {
		if (!value) fitTo(extent);
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
