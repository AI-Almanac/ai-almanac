<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { getBlendForecast, type BlendForecastData, type BlendForecastPoint } from '$lib/api';
	import {
		WEEKS,
		WEEK_LABELS,
		PROB_RAMP,
		legendGradient,
		WINDOW_RAMP,
		rampColor,
		argmax,
		fmtProb,
		fmtDate,
		monthLabel,
		type Week
	} from '$lib/onset';
	import CellInspector from './CellInspector.svelte';

	type Props = { jobId: string };
	let { jobId }: Props = $props();

	let mapHost = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);

	let data = $state<BlendForecastData | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let selectedDate = $state('');
	let selectedWeek = $state<Week>('week1');
	// 'window' colors by the selected window's probability (magnitude); 'expected'
	// collapses the distribution to each point's most-likely window (which window).
	let colorMode = $state<'window' | 'expected'>('window');

	let playing = $state(false);
	let playTimer: ReturnType<typeof setInterval> | null = null;
	let collapsed = $state(false);
	let resizeObserver: ResizeObserver | null = null;

	let tooltipVisible = $state(false);
	let tooltipX = $state(0);
	let tooltipY = $state(0);
	let tooltipLat = $state(0);
	let tooltipLon = $state(0);
	let tooltipProbs = $state<number[] | null>(null);

	let selectedCell = $state<BlendForecastPoint | null>(null);

	const EMPTY_PROBS = [0, 0, 0, 0, 0];

	// Precompute each dot's color + opacity in JS (functional core) so the map
	// paint stays a static `['get', …]`; the mode logic lives here, not in the
	// MapLibre expression.
	function featureStyle(row: number[], week: Week): { color: string; opacity: number } {
		if (colorMode === 'expected') {
			const w = argmax(row);
			// Fainter where the timing is uncertain — a weak plurality reads as
			// "we don't really know when," with a visible floor so no dot vanishes.
			return { color: WINDOW_RAMP[w], opacity: 0.4 + 0.55 * Math.min(1, row[w]) };
		}
		return { color: rampColor(row[WEEKS.indexOf(week)] ?? 0), opacity: 0.9 };
	}

	function buildGeoJson(d: BlendForecastData, date: string, week: Week) {
		const dateIdx = d.issue_dates.indexOf(date);
		return {
			type: 'FeatureCollection' as const,
			features: d.points.map((pt, i) => {
				const row = dateIdx >= 0 ? (pt.probs[dateIdx] ?? EMPTY_PROBS) : EMPTY_PROBS;
				const { color, opacity } = featureStyle(row, week);
				return {
					type: 'Feature' as const,
					geometry: { type: 'Point' as const, coordinates: [pt.lon, pt.lat] },
					properties: { color, opacity, idx: i }
				};
			})
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
				'circle-color': ['get', 'color'],
				'circle-opacity': ['get', 'opacity'],
				'circle-stroke-width': 0.5,
				'circle-stroke-color': 'rgba(255,255,255,0.18)'
			}
		});
		fitToData(d);
	}

	// Frame the map on the region's grid points, leaving room for the left rail
	// and bottom scrubber so no dots hide behind the chrome.
	function fitToData(d: BlendForecastData) {
		if (!map || !d.points.length) return;
		const bounds = new maplibregl.LngLatBounds();
		for (const pt of d.points) bounds.extend([pt.lon, pt.lat]);
		map.fitBounds(bounds, {
			padding: { top: 48, right: 48, bottom: 80, left: collapsed ? 48 : 240 },
			maxZoom: 6,
			duration: 0
		});
	}

	$effect(() => {
		if (mapReady && data && selectedDate) {
			selectedWeek; // track
			colorMode; // track
			updateSource();
		}
	});

	// Returns a CSS calc() that positions a tick/label along the track,
	// keeping it inside the 1.4rem insets on each side.
	function tlLeft(i: number, n: number): string {
		const frac = n > 1 ? i / (n - 1) : 0.5;
		return `calc(1.4rem + ${frac} * (100% - 2.8rem))`;
	}

	// Width of the played-so-far fill, from the track's left inset to tick i.
	function tlWidth(i: number, n: number): string {
		const frac = n > 1 ? i / (n - 1) : 0;
		return `calc(${frac} * (100% - 2.8rem))`;
	}

	const dateIndex = $derived(data ? data.issue_dates.indexOf(selectedDate) : -1);
	const dateCount = $derived(data?.issue_dates.length ?? 0);

	function selectDate(d: string) {
		stopPlay();
		selectedDate = d;
	}

	function stepDate(dir: 1 | -1) {
		stopPlay();
		if (!data) return;
		const next = dateIndex + dir;
		if (next >= 0 && next < data.issue_dates.length) selectedDate = data.issue_dates[next];
	}

	function togglePlay() {
		if (playing) {
			stopPlay();
			return;
		}
		if (!data?.issue_dates.length) return;
		playing = true;
		playTimer = setInterval(() => {
			if (!data) return;
			const next = (data.issue_dates.indexOf(selectedDate) + 1) % data.issue_dates.length;
			selectedDate = data.issue_dates[next];
		}, 900);
	}

	function stopPlay() {
		playing = false;
		if (playTimer) {
			clearInterval(playTimer);
			playTimer = null;
		}
	}

	function monthMarkers(dates: string[]) {
		const seen = new Set<string>();
		return dates.flatMap((d, i) => {
			const ym = d.slice(0, 7);
			if (seen.has(ym)) return [];
			seen.add(ym);
			return [{ label: monthLabel(d), i }];
		});
	}

	// Plain-language statement of what the colors mean, tied to the current
	// selection so the reader never has to infer the reference frame.
	const caption = $derived.by(() => {
		const issued = selectedDate ? `forecast issued ${fmtDate(selectedDate)}` : '';
		if (colorMode === 'expected') {
			return `Most likely onset window per location, ${issued}. Fainter dots mean the timing is less certain.`;
		}
		const thr = data?.onset_threshold;
		const onset = thr != null ? `monsoon onset (rainfall ≥ ${thr} mm)` : 'monsoon onset';
		return `Chance ${onset} begins in ${WEEK_LABELS[selectedWeek]}, ${issued}.`;
	});

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
		map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
		map.on('load', () => {
			mapReady = true;
			if (data) initLayer(data);
		});

		// Keep the GL canvas fitted as the rail collapses/expands (and on any
		// container resize); fires through the width transition for a smooth redraw.
		resizeObserver = new ResizeObserver(() => map?.resize());
		resizeObserver.observe(mapHost);

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

		// Click a grid cell to open its season inspector.
		map.on('click', 'blend-circles', (e) => {
			const idx = e.features?.[0]?.properties?.idx;
			if (typeof idx === 'number' && data) selectedCell = data.points[idx] ?? null;
		});
		map.on('mouseenter', 'blend-circles', () => {
			if (map) map.getCanvas().style.cursor = 'pointer';
		});
		map.on('mouseleave', 'blend-circles', () => {
			if (map) map.getCanvas().style.cursor = '';
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
		stopPlay();
		resizeObserver?.disconnect();
		map?.remove();
		map = null;
	});
</script>

<div class="blend-map-wrap">
	<aside class="control-rail" class:collapsed>
		<div class="rail-top">
			<div class="rail-header">
				<span class="rail-title">Monsoon onset</span>
				<button class="rail-collapse" aria-label="Hide controls" onclick={() => (collapsed = true)}>
					«
				</button>
			</div>
			{#if data?.onset_definition}
				<p class="rail-def">
					{#if data.region_name}<span class="rail-def-region">{data.region_name}</span>{/if}
					{data.onset_definition}
				</p>
			{/if}
		</div>

		<div class="rail-group">
			<span class="rail-label">View</span>
			<div class="mode-toggle">
				<button class:active={colorMode === 'window'} onclick={() => (colorMode = 'window')}>
					By window
				</button>
				<button class:active={colorMode === 'expected'} onclick={() => (colorMode = 'expected')}>
					Expected onset
				</button>
			</div>
		</div>

		{#if colorMode === 'window'}
			<div class="rail-group">
				<span class="rail-label">Onset window</span>
				<div class="week-buttons">
					{#each WEEKS as w (w)}
						<button class="week-btn" class:active={w === selectedWeek} onclick={() => (selectedWeek = w)}>
							{WEEK_LABELS[w]}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<div class="rail-group rail-legend">
			<span class="rail-label">Legend</span>
			<p class="legend-caption">{caption}</p>
			{#if colorMode === 'window'}
				<div class="legend-bar" style="background: {legendGradient}"></div>
				<div class="legend-labels">
					<span>0%</span>
					<span>50%</span>
					<span>100%</span>
				</div>
			{:else}
				<div class="window-swatches">
					{#each WEEKS as w, i (w)}
						<div class="swatch-item">
							<span class="swatch" style="background: {WINDOW_RAMP[i]}"></span>
							<span>{WEEK_LABELS[w]}</span>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</aside>

	<div class="map-area">
		{#if collapsed}
			<button class="rail-reopen" aria-label="Show controls" onclick={() => (collapsed = false)}>
				»
			</button>
		{/if}
		<div class="map-host" bind:this={mapHost}></div>

		{#if selectedCell && data}
			<CellInspector
				point={selectedCell}
				issueDates={data.issue_dates}
				regionName={data.region_name}
				{selectedDate}
				onClose={() => (selectedCell = null)}
			/>
		{/if}

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
					<span class="tt-caption">Monsoon onset timing</span>
					<div class="tt-spark">
						{#each WEEKS as w, i (w)}
							<div class="tt-col" class:active={colorMode === 'window' && w === selectedWeek}>
								<div class="tt-bar-track">
									<div
										class="tt-bar-fill"
										style="height: {Math.max(3, (tooltipProbs[i] ?? 0) * 100)}%; background: {WINDOW_RAMP[
											i
										]}"
									></div>
								</div>
								<span class="tt-val">{fmtProb(tooltipProbs[i] ?? 0)}</span>
								<span class="tt-lbl">{WEEK_LABELS[w]}</span>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}

		{#if dateCount > 0}
			<div class="scrubber">
			<div class="scrub-controls">
				<button
					class="scrub-btn"
					aria-label="Previous forecast"
					disabled={dateIndex <= 0}
					onclick={() => stepDate(-1)}>‹</button
				>
				<button
					class="scrub-btn play"
					aria-label={playing ? 'Pause' : 'Play'}
					onclick={togglePlay}>{playing ? '❙❙' : '▶'}</button
				>
				<button
					class="scrub-btn"
					aria-label="Next forecast"
					disabled={dateIndex >= dateCount - 1}
					onclick={() => stepDate(1)}>›</button
				>
			</div>
			<div class="scrub-meta">
				<span class="scrub-label">Forecast issued</span>
				<span class="scrub-date">{selectedDate ? fmtDate(selectedDate) : '—'}</span>
			</div>
			<div class="scrub-track">
				<div class="tl-track"></div>
				<div class="tl-progress" style="width: {tlWidth(Math.max(0, dateIndex), dateCount)}"></div>
				{#each monthMarkers(data?.issue_dates ?? []) as m (m.label)}
					<span class="tl-month" style="left: {tlLeft(m.i, dateCount)}">{m.label}</span>
				{/each}
				{#each data?.issue_dates ?? [] as d, i (d)}
					<button
						class="tl-tick"
						class:active={d === selectedDate}
						style="left: {tlLeft(i, dateCount)}"
						aria-label={fmtDate(d)}
						onclick={() => selectDate(d)}
					></button>
				{/each}
			</div>
		</div>
		{/if}
	</div>
</div>

<style>
	.blend-map-wrap {
		position: absolute;
		inset: 0;
		display: flex;
		background: #0d1117;
	}

	.map-area {
		position: relative;
		flex: 1;
		min-width: 0;
	}

	.scrubber {
		position: absolute;
		bottom: 0;
		left: 0;
		right: 0;
		z-index: 2;
		display: flex;
		align-items: center;
		gap: 0.9rem;
		height: 3.4rem;
		padding: 0 1rem;
		background: rgba(10, 14, 20, 0.85);
		backdrop-filter: blur(8px);
		border-top: 1px solid rgba(255, 255, 255, 0.06);
		pointer-events: auto;
	}

	.scrub-controls {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		flex: none;
	}

	.scrub-btn {
		width: 1.9rem;
		height: 1.9rem;
		display: flex;
		align-items: center;
		justify-content: center;
		border: 1px solid rgba(255, 255, 255, 0.1);
		border-radius: 0.4rem;
		background: rgba(255, 255, 255, 0.03);
		color: #e8e3d8;
		font-size: 0.9rem;
		line-height: 1;
		cursor: pointer;
		transition:
			background 0.12s,
			border-color 0.12s,
			opacity 0.12s;
	}

	.scrub-btn:hover:not(:disabled) {
		background: rgba(255, 255, 255, 0.09);
		border-color: rgba(255, 255, 255, 0.22);
	}

	.scrub-btn:disabled {
		opacity: 0.3;
		cursor: not-allowed;
	}

	.scrub-btn.play {
		background: rgba(43, 127, 207, 0.25);
		border-color: rgba(43, 127, 207, 0.5);
		font-size: 0.7rem;
	}

	.scrub-btn.play:hover {
		background: rgba(43, 127, 207, 0.4);
	}

	.scrub-meta {
		flex: none;
		display: flex;
		flex-direction: column;
		line-height: 1.15;
		min-width: 4.5rem;
	}

	.scrub-label {
		font-size: 0.56rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: rgba(138, 130, 120, 0.8);
	}

	.scrub-date {
		font-size: 0.95rem;
		font-weight: 800;
		color: #e8e3d8;
		font-variant-numeric: tabular-nums;
	}

	.scrub-track {
		position: relative;
		flex: 1;
		height: 100%;
	}

	.tl-track {
		position: absolute;
		left: 1.4rem;
		right: 1.4rem;
		top: 1.35rem;
		height: 1px;
		background: rgba(255, 255, 255, 0.1);
		pointer-events: none;
	}

	.tl-progress {
		position: absolute;
		left: 1.4rem;
		top: 1.35rem;
		height: 2px;
		transform: translateY(-50%);
		background: rgba(43, 127, 207, 0.6);
		border-radius: 999px;
		pointer-events: none;
	}

	.tl-month {
		position: absolute;
		top: 2rem;
		transform: translateX(-50%);
		font-size: 0.6rem;
		font-weight: 700;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: rgba(138, 130, 120, 0.65);
		pointer-events: none;
		white-space: nowrap;
	}

	.tl-tick {
		position: absolute;
		top: 1.35rem;
		width: 1.5rem;
		height: 1.5rem;
		transform: translate(-50%, -50%);
		background: transparent;
		border: none;
		padding: 0;
		cursor: pointer;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.tl-tick::after {
		content: '';
		width: 0.4rem;
		height: 0.4rem;
		border-radius: 50%;
		background: rgba(200, 192, 180, 0.28);
		border: 1px solid rgba(255, 255, 255, 0.14);
		transition:
			background 0.1s,
			transform 0.1s;
	}

	.tl-tick:hover::after {
		background: rgba(200, 192, 180, 0.6);
		transform: scale(1.5);
	}

	.tl-tick.active::after {
		width: 0.7rem;
		height: 0.7rem;
		background: rgb(43, 127, 207);
		border-color: rgba(43, 127, 207, 0.85);
		box-shadow: 0 0 0 3px rgba(43, 127, 207, 0.25);
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

	.tt-caption {
		font-size: 0.58rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: rgba(138, 130, 120, 0.8);
		margin-top: 0.15rem;
	}

	.tt-spark {
		display: flex;
		gap: 0.3rem;
		align-items: flex-end;
	}

	.tt-col {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.15rem;
		width: 2.4rem;
	}

	.tt-bar-track {
		width: 100%;
		height: 2.2rem;
		display: flex;
		align-items: flex-end;
		border-radius: 0.2rem;
		background: rgba(255, 255, 255, 0.05);
	}

	.tt-bar-fill {
		width: 100%;
		border-radius: 0.2rem;
	}

	.tt-val {
		font-size: 0.62rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: rgba(200, 192, 180, 0.7);
	}

	.tt-lbl {
		font-size: 0.56rem;
		font-weight: 600;
		color: rgba(138, 130, 120, 0.75);
		white-space: nowrap;
	}

	.tt-col.active .tt-val {
		color: #e8e3d8;
	}

	.tt-col.active .tt-bar-track {
		box-shadow: inset 0 0 0 1.5px rgba(87, 165, 255, 0.75);
	}

	.control-rail {
		flex: none;
		width: 14rem;
		z-index: 3;
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 0.9rem;
		background: #10151d;
		border-right: 1px solid rgba(255, 255, 255, 0.08);
		overflow-x: hidden;
		overflow-y: auto;
		transition:
			width 0.22s ease,
			padding 0.22s ease;
	}

	.control-rail.collapsed {
		width: 0;
		padding-left: 0;
		padding-right: 0;
		border-right-width: 0;
	}

	.rail-top {
		min-width: 12.2rem;
	}

	.rail-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.rail-def {
		margin: 0.45rem 0 0;
		font-size: 0.64rem;
		line-height: 1.4;
		color: rgba(138, 130, 120, 0.85);
	}

	.rail-def-region {
		display: block;
		font-size: 0.72rem;
		font-weight: 800;
		color: rgba(232, 227, 216, 0.92);
		margin-bottom: 0.15rem;
	}

	.rail-title {
		font-size: 0.62rem;
		font-weight: 800;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: rgba(200, 192, 180, 0.75);
	}

	.rail-collapse,
	.rail-reopen {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.6rem;
		height: 1.6rem;
		border: 1px solid rgba(255, 255, 255, 0.1);
		border-radius: 0.35rem;
		background: rgba(255, 255, 255, 0.03);
		color: #e8e3d8;
		font-size: 0.85rem;
		line-height: 1;
		cursor: pointer;
		transition:
			background 0.12s,
			border-color 0.12s;
	}

	.rail-collapse:hover,
	.rail-reopen:hover {
		background: rgba(255, 255, 255, 0.09);
		border-color: rgba(255, 255, 255, 0.22);
	}

	.rail-reopen {
		position: absolute;
		top: 0.8rem;
		left: 0.8rem;
		z-index: 3;
		width: 2rem;
		height: 2rem;
		background: rgba(10, 14, 20, 0.85);
		backdrop-filter: blur(8px);
	}

	.rail-group {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		min-width: 12.2rem;
	}

	.rail-legend {
		padding-top: 0.9rem;
		border-top: 1px solid rgba(255, 255, 255, 0.08);
	}

	.rail-label {
		font-size: 0.58rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: rgba(138, 130, 120, 0.85);
	}

	.mode-toggle {
		display: flex;
		gap: 0.3rem;
	}

	.mode-toggle button {
		flex: 1;
		border: 1px solid rgba(255, 255, 255, 0.1);
		border-radius: 0.35rem;
		background: rgba(255, 255, 255, 0.03);
		color: rgba(200, 192, 180, 0.7);
		font-size: 0.7rem;
		font-weight: 700;
		padding: 0.4rem 0.2rem;
		cursor: pointer;
		transition:
			background 0.12s,
			color 0.12s,
			border-color 0.12s;
	}

	.mode-toggle button:hover {
		color: #e8e3d8;
		border-color: rgba(255, 255, 255, 0.22);
	}

	.mode-toggle button.active {
		background: rgb(43, 127, 207);
		border-color: rgb(43, 127, 207);
		color: #fff;
	}

	.week-buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
	}

	.week-btn {
		flex: 1 1 3.2rem;
		border: 1px solid rgba(255, 255, 255, 0.1);
		border-radius: 0.3rem;
		background: rgba(255, 255, 255, 0.03);
		color: rgba(200, 192, 180, 0.7);
		font-size: 0.72rem;
		font-weight: 700;
		padding: 0.3rem 0.2rem;
		cursor: pointer;
		white-space: nowrap;
		transition:
			background 0.12s,
			color 0.12s,
			border-color 0.12s;
	}

	.week-btn:hover {
		color: #e8e3d8;
		border-color: rgba(255, 255, 255, 0.18);
	}

	.week-btn.active {
		background: rgba(43, 127, 207, 0.25);
		border-color: rgba(43, 127, 207, 0.55);
		color: #e8e3d8;
	}

	.legend-caption {
		font-size: 0.67rem;
		font-weight: 600;
		line-height: 1.4;
		color: rgba(200, 192, 180, 0.78);
		margin: 0 0 0.5rem;
	}

	.window-swatches {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 0.6rem;
	}

	.swatch-item {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.66rem;
		font-weight: 700;
		color: rgba(232, 227, 216, 0.9);
		white-space: nowrap;
	}

	.swatch {
		width: 0.8rem;
		height: 0.8rem;
		border-radius: 0.2rem;
		box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
		flex: none;
	}

	.legend-bar {
		height: 0.4rem;
		border-radius: 999px;
		box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.1);
	}

	.legend-labels {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-top: 0.28rem;
		font-size: 0.72rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: rgba(232, 227, 216, 0.9);
	}
</style>
