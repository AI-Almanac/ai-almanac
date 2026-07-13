<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { authHeaders } from '$lib/auth';
	import { cogPointUrl, cogTileTemplate, isBackendUrl, type ForecastManifest } from '$lib/api';

	type Props = {
		jobId: string;
		modelId: string;
		modelName: string;
		manifest: ForecastManifest | null;
		variable: string;
		leadHour: number;
		label: string;
	};

	let { jobId, modelId, manifest, variable, leadHour, label }: Props = $props();

	let mapHost = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);
	let fitted = false;

	let tooltipVisible = $state(false);
	let tooltipX = $state(0);
	let tooltipY = $state(0);
	let tooltipLat = $state(0);
	let tooltipLon = $state(0);
	let tooltipValue = $state<number | null>(null);
	let fetchTimer: ReturnType<typeof setTimeout> | null = null;
	let fetchAbort: AbortController | null = null;

	const layerLead = new Map<string, number>();
	let managedLayerIds: string[] = [];
	let managedSourceIds: string[] = [];

	function product(v: string, lh: number) {
		return manifest?.map_products?.[v]?.[String(lh)] ?? null;
	}

	const activeProduct = $derived(manifest ? product(variable, leadHour) : null);

	function clearLayers() {
		if (!map) return;
		for (let i = managedLayerIds.length - 1; i >= 0; i--) {
			if (map.getLayer(managedLayerIds[i])) map.removeLayer(managedLayerIds[i]);
			if (map.getSource(managedSourceIds[i])) map.removeSource(managedSourceIds[i]);
		}
		managedLayerIds = [];
		managedSourceIds = [];
		layerLead.clear();
	}

	function addLayer(lh: number, active: boolean) {
		if (!map || !manifest) return;
		const p = product(variable, lh);
		if (!p) return;
		const sid = `fc-src-${modelId}-${lh}`;
		const lid = `fc-lyr-${modelId}-${lh}`;
		if (map.getSource(sid) || map.getLayer(lid)) return;
		const tileUrl = cogTileTemplate(jobId, `${modelId}/${p.cog}`, [p.min, p.max]);
		const [west, south, east, north] = p.bounds_lonlat;
		map.addSource(sid, {
			type: 'raster',
			tiles: [tileUrl],
			tileSize: 256,
			bounds: [west, south, east, north],
			attribution: ''
		});
		map.addLayer({
			id: lid,
			type: 'raster',
			source: sid,
			layout: { visibility: active ? 'visible' : 'none' },
			paint: {
				'raster-opacity': active ? 0.85 : 0,
				'raster-opacity-transition': { duration: 350, delay: 0 }
			}
		});
		managedLayerIds.push(lid);
		managedSourceIds.push(sid);
		layerLead.set(lid, lh);
	}

	function applyLeadHourOpacity(targetLh: number) {
		if (!map) return;
		for (const lid of managedLayerIds) {
			if (map.getLayer(lid)) {
				const isActive = layerLead.get(lid) === targetLh;
				map.setLayoutProperty(lid, 'visibility', isActive ? 'visible' : 'none');
				map.setPaintProperty(lid, 'raster-opacity', isActive ? 0.85 : 0);
			}
		}
	}

	function fmtVal(value: number): string {
		return Math.abs(value) >= 10 ? Math.round(value).toString() : value.toFixed(1);
	}

	function fmtCoords(lat: number, lon: number): string {
		const latStr = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? 'N' : 'S'}`;
		const lonStr = `${Math.abs(lon).toFixed(2)}°${lon >= 0 ? 'E' : 'W'}`;
		return `${latStr}  ${lonStr}`;
	}

	function fmtValidTime(isoInit: string | null, offsetHours: number): string {
		if (!isoInit) return `+${offsetHours}h`;
		const d = new Date(isoInit);
		if (Number.isNaN(d.getTime())) return `+${offsetHours}h`;
		d.setUTCHours(d.getUTCHours() + offsetHours);
		return new Intl.DateTimeFormat(undefined, {
			month: 'short',
			day: 'numeric',
			hour: 'numeric',
			minute: '2-digit',
			timeZoneName: 'short'
		}).format(d);
	}

	const validTimeStr = $derived(fmtValidTime(manifest?.init_time ?? null, leadHour));

	function fitToBounds() {
		if (!map || fitted || !activeProduct) return;
		const [west, south, east, north] = activeProduct.bounds_lonlat;
		map.fitBounds(
			[
				[west, south],
				[east, north]
			],
			{ padding: 28, duration: 280 }
		);
		fitted = true;
	}

	function rebuildLayers() {
		if (!map || !mapReady || !manifest) return;
		fitted = false;
		clearLayers();
		for (const lh of manifest.lead_hours) addLayer(lh, lh === leadHour);
		fitToBounds();
	}

	// Rebuild all lead-hour layers when the model or variable changes.
	// leadHour is untracked so scrubbing doesn't trigger a full rebuild.
	$effect(() => {
		if (!mapReady) return;
		manifest;
		modelId;
		variable;
		untrack(() => leadHour);
		rebuildLayers();
	});

	// Cross-fade to the active lead hour without touching sources.
	$effect(() => {
		if (!mapReady) return;
		applyLeadHourOpacity(leadHour);
	});

	onMount(() => {
		if (!mapHost) return;
		map = new maplibregl.Map({
			container: mapHost,
			style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
			center: [20, 10],
			zoom: 1.8,
			attributionControl: false,
			transformRequest: (url) => {
				if (!isBackendUrl(url)) return { url };
				const headers = authHeaders() as Record<string, string>;
				return Object.keys(headers).length ? { url, headers } : { url };
			}
		});
		map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
		map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
		map.on('load', () => {
			mapReady = true;
			const attrib = mapHost?.querySelector<HTMLDetailsElement>('.maplibregl-ctrl-attrib');
			if (attrib) {
				attrib.classList.remove('maplibregl-compact-show');
				attrib.removeAttribute('open');
			}
		});

		map.on('mousemove', (e) => {
			tooltipX = e.point.x + 16;
			tooltipY = e.point.y - 10;
			tooltipLat = e.lngLat.lat;
			tooltipLon = e.lngLat.lng;
			tooltipVisible = true;

			if (fetchTimer) clearTimeout(fetchTimer);
			if (fetchAbort) fetchAbort.abort();
			tooltipValue = null;

			const p = activeProduct;
			if (!p) return;
			const { lat, lng: lon } = e.lngLat;
			fetchTimer = setTimeout(async () => {
				fetchAbort = new AbortController();
				try {
					const url = cogPointUrl(jobId, `${modelId}/${p.cog}`, lon, lat);
					const resp = await fetch(url, { headers: authHeaders(), signal: fetchAbort.signal });
					if (!resp.ok) return;
					const data = await resp.json();
					const raw: unknown = Array.isArray(data.values) ? data.values[0] : null;
					if (typeof raw !== 'number' || !Number.isFinite(raw) || Math.abs(raw) > 1e20) return;
					tooltipValue = raw;
				} catch {
					// aborted or network error — no tooltip value shown
				}
			}, 120);
		});

		map.on('mouseout', () => {
			tooltipVisible = false;
			if (fetchTimer) clearTimeout(fetchTimer);
			if (fetchAbort) fetchAbort.abort();
		});
	});

	onDestroy(() => {
		if (fetchTimer) clearTimeout(fetchTimer);
		if (fetchAbort) fetchAbort.abort();
		map?.remove();
		map = null;
	});
</script>

<div class="forecast-map">
	<div class="map-host" bind:this={mapHost}></div>

	<div class="hud">
		<div class="hud-label">
			<strong>{manifest?.model_name ?? modelId}</strong>
			<span class="hud-meta">{label}<span class="hud-sep">·</span>Forecast for {validTimeStr}</span>
		</div>
	</div>

	{#if tooltipVisible}
		<div class="map-tooltip" style="left: {tooltipX}px; top: {tooltipY}px">
			<span class="tt-coords">{fmtCoords(tooltipLat, tooltipLon)}</span>
			{#if tooltipValue !== null && activeProduct}
				<span class="tt-value"
					>{fmtVal(tooltipValue)}<span class="tt-unit"> {activeProduct.unit}</span></span
				>
			{/if}
		</div>
	{/if}

	{#if activeProduct}
		<div class="legend">
			<div class="legend-bar"></div>
			<div class="legend-labels">
				<span>{fmtVal(activeProduct.min)}</span>
				<span class="legend-unit">{activeProduct.unit}</span>
				<span>{fmtVal(activeProduct.max)}</span>
			</div>
		</div>
	{/if}

	{#if !activeProduct}
		<div class="note">No {label} data for this lead time.</div>
	{/if}
</div>

<style>
	.forecast-map {
		position: absolute;
		inset: 0;
		background: #0d1117;
	}

	.map-host {
		position: absolute;
		inset: 0;
	}

	:global(.forecast-map .maplibregl-canvas-container),
	:global(.forecast-map .maplibregl-canvas) {
		width: 100% !important;
		height: 100% !important;
	}

	:global(.forecast-map .maplibregl-ctrl-bottom-right) {
		bottom: 0.5rem;
		right: 0.5rem;
	}

	.hud {
		position: absolute;
		top: 0.8rem;
		left: 0.8rem;
		right: 3.5rem;
		z-index: 2;
		pointer-events: none;
	}

	.hud-label {
		border: 1px solid rgba(255, 255, 255, 0.07);
		border-radius: 0.45rem;
		background: rgba(10, 14, 20, 0.82);
		backdrop-filter: blur(8px);
		color: #e8e3d8;
		padding: 0.45rem 0.7rem;
		display: flex;
		flex-direction: column;
		gap: 0.08rem;
		max-width: max-content;
	}

	.hud-label strong {
		font-size: 0.88rem;
		font-weight: 800;
		white-space: nowrap;
		letter-spacing: -0.01em;
	}

	.hud-meta {
		font-size: 0.72rem;
		font-weight: 600;
		color: rgba(200, 192, 180, 0.65);
		white-space: nowrap;
	}

	.hud-sep {
		margin: 0 0.35em;
		opacity: 0.5;
	}

	.legend {
		position: absolute;
		bottom: 2.5rem;
		left: 50%;
		transform: translateX(-50%);
		z-index: 2;
		border: 1px solid rgba(255, 255, 255, 0.07);
		border-radius: 0.5rem;
		background: rgba(10, 14, 20, 0.82);
		backdrop-filter: blur(8px);
		padding: 0.45rem 0.8rem 0.5rem;
		pointer-events: none;
		min-width: 11rem;
	}

	.legend-bar {
		height: 0.35rem;
		border-radius: 999px;
		background: linear-gradient(
			to right,
			rgb(40, 44, 98),
			rgb(43, 127, 207),
			rgb(87, 197, 173),
			rgb(226, 222, 93),
			rgb(232, 132, 54),
			rgb(116, 35, 38)
		);
	}

	.legend-labels {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-top: 0.28rem;
		font-size: 0.72rem;
		font-weight: 700;
		color: rgba(232, 227, 216, 0.9);
	}

	.legend-unit {
		color: rgba(138, 130, 120, 0.8);
		font-size: 0.67rem;
		font-weight: 600;
	}

	.map-tooltip {
		position: absolute;
		z-index: 10;
		pointer-events: none;
		border: 1px solid rgba(255, 255, 255, 0.12);
		border-radius: 0.4rem;
		background: rgba(13, 17, 23, 0.9);
		backdrop-filter: blur(6px);
		padding: 0.35rem 0.6rem;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		white-space: nowrap;
	}

	.tt-coords {
		font-size: 0.72rem;
		font-weight: 700;
		color: #8a8278;
		letter-spacing: 0.02em;
	}

	.tt-value {
		font-size: 0.88rem;
		font-weight: 800;
		color: #e8e3d8;
	}

	.tt-unit {
		font-size: 0.75rem;
		font-weight: 600;
		color: #8a8278;
	}

	.note {
		position: absolute;
		left: 0.9rem;
		bottom: 2.5rem;
		z-index: 2;
		max-width: min(36rem, calc(100% - 1.8rem));
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 0.45rem;
		background: rgba(13, 17, 23, 0.82);
		backdrop-filter: blur(6px);
		color: #8a8278;
		padding: 0.45rem 0.65rem;
		font-size: 0.8rem;
		font-weight: 700;
	}
</style>
