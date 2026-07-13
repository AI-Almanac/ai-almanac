<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { getBlendForecast, type BlendForecastData } from '$lib/api';

	type Props = { jobId: string };
	let { jobId }: Props = $props();

	const WEEKS = ['week1', 'week2', 'week3', 'week4', 'later'] as const;
	type Week = (typeof WEEKS)[number];
	const WEEK_LABELS: Record<Week, string> = {
		week1: 'Week 1',
		week2: 'Week 2',
		week3: 'Week 3',
		week4: 'Week 4',
		later: 'Later'
	};

	let mapHost = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);

	let data = $state<BlendForecastData | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let selectedDate = $state('');
	let selectedWeek = $state<Week>('week1');

	let tooltipVisible = $state(false);
	let tooltipX = $state(0);
	let tooltipY = $state(0);
	let tooltipLat = $state(0);
	let tooltipLon = $state(0);
	let tooltipProbs = $state<number[] | null>(null);

	const weekIdx: Record<Week, number> = {
		week1: 0,
		week2: 1,
		week3: 2,
		week4: 3,
		later: 4
	};

	function buildGeoJson(d: BlendForecastData, date: string, week: Week) {
		const dateIdx = d.issue_dates.indexOf(date);
		const wi = weekIdx[week];
		return {
			type: 'FeatureCollection' as const,
			features: d.points.map((pt) => ({
				type: 'Feature' as const,
				geometry: { type: 'Point' as const, coordinates: [pt.lon, pt.lat] },
				properties: { value: dateIdx >= 0 ? (pt.probs[dateIdx]?.[wi] ?? 0) : 0 }
			}))
		};
	}

	function updateSource() {
		if (!map || !data || !selectedDate) return;
		const src = map.getSource('blend') as maplibregl.GeoJSONSource | undefined;
		if (src) src.setData(buildGeoJson(data, selectedDate, selectedWeek));
	}

	function initLayer(d: BlendForecastData) {
		if (!map) return;
		const geojson = buildGeoJson(d, selectedDate, selectedWeek);
		if (map.getSource('blend')) {
			(map.getSource('blend') as maplibregl.GeoJSONSource).setData(geojson);
			return;
		}
		map.addSource('blend', { type: 'geojson', data: geojson });
		map.addLayer({
			id: 'blend-circles',
			type: 'circle',
			source: 'blend',
			paint: {
				'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 4, 5, 10, 8, 16],
				'circle-color': [
					'interpolate',
					['linear'],
					['get', 'value'],
					0, '#2b2c7a',
					0.15, '#2b7fcf',
					0.35, '#57c5ad',
					0.5, '#e2de5d',
					0.7, '#e88436',
					1, '#742326'
				],
				'circle-opacity': 0.85,
				'circle-stroke-width': 0.5,
				'circle-stroke-color': 'rgba(0,0,0,0.3)'
			}
		});
	}

	$effect(() => {
		if (mapReady && data && selectedDate) {
			selectedWeek; // track
			updateSource();
		}
	});

	function fmtProb(v: number) {
		return `${(v * 100).toFixed(0)}%`;
	}

	function nearestPoint(lng: number, lat: number) {
		if (!data) return null;
		let best: { probs: number[][] } | null = null;
		let bestDist = Infinity;
		for (const pt of data.points) {
			const d = (pt.lon - lng) ** 2 + (pt.lat - lat) ** 2;
			if (d < bestDist) {
				bestDist = d;
				best = pt;
			}
		}
		return bestDist < 4 ? best : null; // ~2 degrees snap radius
	}

	onMount(async () => {
		if (!mapHost) return;
		map = new maplibregl.Map({
			container: mapHost,
			style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
			center: [20, 10],
			zoom: 1.8,
			attributionControl: false
		});
		map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
		map.on('load', () => {
			mapReady = true;
			if (data) initLayer(data);
		});

		map.on('mousemove', (e) => {
			tooltipX = e.point.x + 16;
			tooltipY = e.point.y - 10;
			tooltipLat = e.lngLat.lat;
			tooltipLon = e.lngLat.lng;
			tooltipVisible = true;
			const pt = nearestPoint(e.lngLat.lng, e.lngLat.lat);
			const dateIdx = data ? data.issue_dates.indexOf(selectedDate) : -1;
			tooltipProbs = pt && dateIdx >= 0 ? (pt.probs[dateIdx] ?? null) : null;
		});
		map.on('mouseout', () => {
			tooltipVisible = false;
			tooltipProbs = null;
		});

		try {
			const d = await getBlendForecast(jobId);
			data = d;
			if (d.issue_dates.length) selectedDate = d.issue_dates[0];
			if (mapReady) initLayer(d);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load blend forecast';
		} finally {
			loading = false;
		}
	});

	onDestroy(() => {
		map?.remove();
		map = null;
	});
</script>

<div class="blend-map-wrap">
	<div class="map-controls">
		<label>
			Issue date
			<select bind:value={selectedDate} disabled={!data}>
				{#each data?.issue_dates ?? [] as d (d)}
					<option value={d}>{d}</option>
				{/each}
			</select>
		</label>
		<label>
			Onset window
			<select bind:value={selectedWeek}>
				{#each WEEKS as w (w)}
					<option value={w}>{WEEK_LABELS[w]}</option>
				{/each}
			</select>
		</label>
	</div>

	<div class="map-host" bind:this={mapHost}></div>

	{#if loading}
		<div class="overlay muted">Loading blend forecast…</div>
	{:else if error}
		<div class="overlay error">{error}</div>
	{:else if !data?.points.length}
		<div class="overlay muted">No blend forecast data for this job.</div>
	{/if}

	{#if tooltipVisible && !loading}
		<div class="map-tooltip" style="left: {tooltipX}px; top: {tooltipY}px">
			<span class="tt-coords"
				>{Math.abs(tooltipLat).toFixed(2)}°{tooltipLat >= 0 ? 'N' : 'S'}&nbsp;&nbsp;{Math.abs(
					tooltipLon
				).toFixed(2)}°{tooltipLon >= 0 ? 'E' : 'W'}</span
			>
			{#if tooltipProbs}
				<div class="tt-probs">
					{#each WEEKS as w, i (w)}
						<span class:active={w === selectedWeek}
							>{WEEK_LABELS[w]}: {fmtProb(tooltipProbs[i] ?? 0)}</span
						>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<div class="legend">
		<div class="legend-bar"></div>
		<div class="legend-labels">
			<span>0%</span>
			<span class="legend-unit">Onset probability — {WEEK_LABELS[selectedWeek]}</span>
			<span>100%</span>
		</div>
	</div>
</div>

<style>
	.blend-map-wrap {
		position: absolute;
		inset: 0;
		background: #0d1117;
	}

	.map-controls {
		position: absolute;
		top: 0.8rem;
		left: 0.8rem;
		z-index: 2;
		display: flex;
		gap: 0.6rem;
		pointer-events: auto;
	}

	.map-controls label {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.72rem;
		font-weight: 700;
		color: rgba(200, 192, 180, 0.65);
		border: 1px solid rgba(255, 255, 255, 0.07);
		border-radius: 0.45rem;
		background: rgba(10, 14, 20, 0.82);
		backdrop-filter: blur(8px);
		padding: 0.4rem 0.6rem;
	}

	.map-controls select {
		background: transparent;
		border: none;
		color: #e8e3d8;
		font-size: 0.8rem;
		font-weight: 700;
		cursor: pointer;
		outline: none;
		padding: 0;
	}

	.map-host {
		position: absolute;
		inset: 0;
	}

	:global(.blend-map-wrap .maplibregl-canvas-container),
	:global(.blend-map-wrap .maplibregl-canvas) {
		width: 100% !important;
		height: 100% !important;
	}

	.overlay {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 0.85rem;
		font-weight: 600;
		pointer-events: none;
		z-index: 3;
	}

	.overlay.error {
		color: #f87171;
	}

	.overlay.muted {
		color: rgba(138, 130, 120, 0.8);
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
		gap: 0.2rem;
		white-space: nowrap;
	}

	.tt-coords {
		font-size: 0.72rem;
		font-weight: 700;
		color: #8a8278;
		letter-spacing: 0.02em;
	}

	.tt-probs {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		font-size: 0.78rem;
		font-weight: 600;
		color: rgba(200, 192, 180, 0.6);
	}

	.tt-probs .active {
		color: #e8e3d8;
		font-weight: 800;
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
		min-width: 14rem;
	}

	.legend-bar {
		height: 0.35rem;
		border-radius: 999px;
		background: linear-gradient(
			to right,
			rgb(43, 44, 122),
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
</style>
