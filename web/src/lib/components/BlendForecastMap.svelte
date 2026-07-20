<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { getBlendForecast, type BlendForecastData, type BlendForecastPoint } from '$lib/api';
	import {
		WEEKS,
		WEEK_LABELS,
		probGradient,
		WINDOW_RAMP,
		ONSET_PASSED_COLOR,
		rampColor,
		consensusOnsetDay,
		onsetHasPassed,
		argmax,
		fmtProb,
		fmtDate,
		monthLabel,
		type Week
	} from '$lib/onset';
	import CellInspector from './CellInspector.svelte';
	import MapTooltip from './MapTooltip.svelte';
	import { BASEMAP_STYLES, isDarkBasemap, type BasemapStyleId } from '$lib/basemaps';
	import { formatLatLon } from '$lib/geo';

	type Props = { jobId: string };
	let { jobId }: Props = $props();

	let mapHost = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);

	let selectedBasemap = $state<BasemapStyleId>('carto-dark');
	let appliedBasemap: BasemapStyleId = 'carto-dark';
	let fullscreen = $state(false);
	const isDark = $derived(isDarkBasemap(selectedBasemap));

	function basemapStyle() {
		return BASEMAP_STYLES.find((s) => s.id === selectedBasemap) ?? BASEMAP_STYLES[0];
	}

	// Dot outlines flip with the basemap so a cell stays visible on light or
	// dark tiles regardless of its fill.
	function dotStroke(): string {
		return isDark ? 'rgba(255,255,255,0.35)' : 'rgba(20,25,35,0.4)';
	}

	let data = $state<BlendForecastData | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let selectedDate = $state('');
	let selectedWeek = $state<Week>('week1');
	// 'window' colors by the selected window's probability (magnitude); 'expected'
	// collapses the distribution to each point's most-likely window (which window).
	let colorMode = $state<'window' | 'expected'>('window');

	// Which end of the ordinal ramp is the soonest window. 'yellow' (imminent =
	// hot) is our default; 'purple' matches the science team's static legend.
	let soonestColor = $state<'yellow' | 'purple'>('yellow');
	// The toggle flips the whole plasma direction: the vivid end marks both the
	// soonest window and the highest probability.
	const reversed = $derived(soonestColor === 'purple');
	const windowRamp = $derived(reversed ? [...WINDOW_RAMP].reverse() : WINDOW_RAMP);

	// Per-cell estimated onset day (index-aligned to data.points), used to gray a
	// cell once the shown forecast was issued after onset likely occurred.
	const cellConsensus = $derived.by(() =>
		data ? data.points.map((pt) => consensusOnsetDay(data!.issue_dates, pt.probs)) : []
	);

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

	// Smallest positive gap between unique coordinate values — the native grid
	// step. Using the min (not the mean) keeps cells from overlapping when the
	// grid has occasional gaps.
	function minPositiveDiff(values: number[]): number | null {
		const uniq = [...new Set(values)].sort((a, b) => a - b);
		let min = Infinity;
		for (let i = 1; i < uniq.length; i++) {
			const d = uniq[i] - uniq[i - 1];
			if (d > 0 && d < min) min = d;
		}
		return Number.isFinite(min) ? min : null;
	}

	// Cell size inferred from the data so squares tile the native lat/lon grid
	// instead of overlapping like fixed-radius dots — Ethiopia's grid is far
	// finer than India's, and a pixel radius can't serve both.
	const gridStep = $derived.by(() => {
		const fallback = { dx: 0.25, dy: 0.25 };
		if (!data?.points.length) return fallback;
		return {
			dx: minPositiveDiff(data.points.map((p) => p.lon)) ?? fallback.dx,
			dy: minPositiveDiff(data.points.map((p) => p.lat)) ?? fallback.dy
		};
	});

	// Precompute each dot's color + opacity in JS (functional core) so the map
	// paint stays a static `['get', …]`; the mode logic lives here, not in the
	// MapLibre expression.
	function featureStyle(
		row: number[],
		week: Week,
		passed: boolean
	): { color: string; opacity: number } {
		// Onset already occurred by this issue date: the forward outlook is stale,
		// so drain the color to a dim gray rather than show a misleading dot.
		if (passed) return { color: ONSET_PASSED_COLOR, opacity: 0.45 };
		if (colorMode === 'expected') {
			const w = argmax(row);
			// Fainter where the timing is uncertain — a weak plurality reads as
			// "we don't really know when," with a visible floor so no dot vanishes.
			return { color: windowRamp[w], opacity: 0.4 + 0.55 * Math.min(1, row[w]) };
		}
		return { color: rampColor(row[WEEKS.indexOf(week)] ?? 0, reversed), opacity: 0.9 };
	}

	function buildGeoJson(d: BlendForecastData, date: string, week: Week) {
		const dateIdx = d.issue_dates.indexOf(date);
		const hx = gridStep.dx / 2;
		const hy = gridStep.dy / 2;
		return {
			type: 'FeatureCollection' as const,
			features: d.points.map((pt, i) => {
				const row = dateIdx >= 0 ? (pt.probs[dateIdx] ?? EMPTY_PROBS) : EMPTY_PROBS;
				const passed = dateIdx >= 0 && onsetHasPassed(date, cellConsensus[i] ?? null);
				const { color, opacity } = featureStyle(row, week, passed);
				// A square covering the point's grid cell, so cells tile the grid
				// and scale with zoom (geographic units) rather than overlapping.
				const ring = [
					[pt.lon - hx, pt.lat - hy],
					[pt.lon + hx, pt.lat - hy],
					[pt.lon + hx, pt.lat + hy],
					[pt.lon - hx, pt.lat + hy],
					[pt.lon - hx, pt.lat - hy]
				];
				return {
					type: 'Feature' as const,
					geometry: { type: 'Polygon' as const, coordinates: [ring] },
					properties: { color, opacity, idx: i, passed }
				};
			})
		};
	}

	function updateSource() {
		if (!map || !data || !selectedDate) return;
		const src = map.getSource('blend') as maplibregl.GeoJSONSource | undefined;
		if (src) src.setData(buildGeoJson(data, selectedDate, selectedWeek));
	}

	// Nudge the dark basemap so land reads as a surface a shade above the void
	// and water sits below it — gives the dot field something to rest on.
	// Carto layer names vary, so match defensively and skip anything absent.
	function liftBasemap() {
		if (!map || !isDark) return;
		try {
			for (const layer of map.getStyle().layers ?? []) {
				if (layer.type === 'background')
					map.setPaintProperty(layer.id, 'background-color', '#151b24');
				else if (layer.id.includes('water'))
					map.setPaintProperty(layer.id, 'fill-color', '#0b0e13');
			}
		} catch {
			/* basemap has no matching layers; leave the default style */
		}
	}

	function initLayer(d: BlendForecastData, { fit = true } = {}) {
		if (!map) return;
		const geojson = buildGeoJson(d, selectedDate, selectedWeek);
		if (map.getSource('blend')) {
			(map.getSource('blend') as maplibregl.GeoJSONSource).setData(geojson);
			return;
		}
		map.addSource('blend', { type: 'geojson', data: geojson });
		map.addLayer({
			id: 'blend-cells',
			type: 'fill',
			source: 'blend',
			paint: {
				'fill-color': ['get', 'color'],
				'fill-opacity': ['get', 'opacity'],
				'fill-outline-color': dotStroke()
			}
		});
		if (fit) fitToData(d);
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
			soonestColor; // track
			updateSource();
		}
	});

	// Swap the basemap tiles without moving the camera; setStyle drops our
	// custom layer, so re-add it (and re-lift the land) once the style loads.
	$effect(() => {
		const next = selectedBasemap;
		if (!map || !mapReady || next === appliedBasemap) return;
		appliedBasemap = next;
		map.once('style.load', () => {
			liftBasemap();
			if (data) initLayer(data, { fit: false });
		});
		map.setStyle(basemapStyle().url);
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
		if (colorMode === 'expected') {
			return 'Most likely onset window per location. Fainter dots mean the timing is less certain.';
		}
		const thr = data?.onset_threshold;
		const onset = thr != null ? `monsoon onset (rainfall ≥ ${thr} mm)` : 'monsoon onset';
		return `Chance ${onset} begins in ${WEEK_LABELS[selectedWeek]}.`;
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
			style: basemapStyle().url,
			center: [20, 10],
			zoom: 1.8,
			attributionControl: false
		});
		map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
		map.on('load', () => {
			mapReady = true;
			liftBasemap();
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
		map.on('click', 'blend-cells', (e) => {
			const idx = e.features?.[0]?.properties?.idx;
			if (typeof idx === 'number' && data) selectedCell = data.points[idx] ?? null;
		});
		map.on('mouseenter', 'blend-cells', () => {
			if (map) map.getCanvas().style.cursor = 'pointer';
		});
		map.on('mouseleave', 'blend-cells', () => {
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

<div class="blend-map-wrap" class:fullscreen>
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

		<div class="rail-group">
			<span class="rail-label">Soonest onset color</span>
			<div class="mode-toggle">
				<button class:active={soonestColor === 'yellow'} onclick={() => (soonestColor = 'yellow')}>
					Yellow
				</button>
				<button class:active={soonestColor === 'purple'} onclick={() => (soonestColor = 'purple')}>
					Purple
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

		<div class="rail-group">
			<span class="rail-label" id="basemap-label">Base map</span>
			<select
				class="rail-select"
				aria-labelledby="basemap-label"
				bind:value={selectedBasemap}
			>
				{#each BASEMAP_STYLES as style (style.id)}
					<option value={style.id}>{style.label}</option>
				{/each}
			</select>
		</div>

		<div class="rail-group rail-legend">
			<span class="rail-label">Legend</span>
			<p class="legend-caption">{caption}</p>
			{#if colorMode === 'window'}
				<div class="legend-bar" style="background: {probGradient(reversed)}"></div>
				<div class="legend-ticks">
					<span></span>
					<span></span>
					<span></span>
				</div>
				<div class="legend-labels">
					<span>0%</span>
					<span>50%</span>
					<span>100%</span>
				</div>
			{:else}
				<div class="window-swatches">
					{#each WEEKS as w, i (w)}
						<div class="swatch-item">
							<span class="swatch" style="background: {windowRamp[i]}"></span>
							<span>{WEEK_LABELS[w]}</span>
						</div>
					{/each}
				</div>
			{/if}
			<div class="swatch-item passed-note">
				<span class="swatch" style="background: {ONSET_PASSED_COLOR}"></span>
				<span>Peak onset window passed</span>
			</div>
			<p class="legend-note">
				Gray means this forecast was issued after the window when onset was most likely — the peak
				probability has passed, so the outlook ahead no longer applies. This is estimated from the
				forecasts, not a confirmation that onset occurred.
			</p>
		</div>
	</aside>

	<div class="map-area">
		{#if collapsed}
			<button class="rail-reopen" aria-label="Show controls" onclick={() => (collapsed = false)}>
				»
			</button>
		{/if}
		<button
			class="fullscreen-btn"
			aria-label={fullscreen ? 'Exit full screen' : 'View full screen'}
			title={fullscreen ? 'Exit full screen' : 'View full screen'}
			onclick={() => (fullscreen = !fullscreen)}
		>
			{fullscreen ? '⤡' : '⤢'}
		</button>
		<div class="map-host" bind:this={mapHost}></div>

		{#if selectedCell && data}
			<CellInspector
				point={selectedCell}
				issueDates={data.issue_dates}
				regionName={data.region_name}
				{selectedDate}
				{soonestColor}
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
			<MapTooltip x={tooltipX} y={tooltipY} coords={formatLatLon(tooltipLat, tooltipLon)}>
				{#if tooltipProbs}
					<span class="tt-caption">Monsoon onset timing</span>
					<div class="tt-spark">
						{#each WEEKS as w, i (w)}
							<div class="tt-col" class:active={colorMode === 'window' && w === selectedWeek}>
								<div class="tt-bar-track">
									<div
										class="tt-bar-fill"
										style="height: {Math.max(3, (tooltipProbs[i] ?? 0) * 100)}%; background: {windowRamp[
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
			</MapTooltip>
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
		background: var(--color-bg);
	}

	.map-area {
		position: relative;
		flex: 1;
		min-width: 0;
	}

	.blend-map-wrap.fullscreen {
		position: fixed;
		inset: 0;
		z-index: 1000;
	}

	.fullscreen-btn {
		position: absolute;
		top: 0.6rem;
		right: 3.4rem;
		z-index: 3;
		width: 1.9rem;
		height: 1.9rem;
		display: flex;
		align-items: center;
		justify-content: center;
		border: 1px solid rgba(0, 0, 0, 0.12);
		border-radius: 0.25rem;
		background: rgba(255, 255, 255, 0.9);
		color: #333;
		font-size: 1rem;
		line-height: 1;
		cursor: pointer;
		box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
		transition: background 0.1s;
	}

	.fullscreen-btn:hover {
		background: #fff;
		color: #111;
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
		background: rgba(255, 255, 255, 0.9);
		backdrop-filter: blur(8px);
		border-top: 1px solid rgba(0, 0, 0, 0.08);
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
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		background: #fff;
		color: #444;
		font-size: 0.9rem;
		line-height: 1;
		cursor: pointer;
		transition:
			background 0.12s,
			border-color 0.12s,
			opacity 0.12s;
	}

	.scrub-btn:hover:not(:disabled) {
		background: var(--color-surface-muted);
		border-color: var(--color-text-dim);
		color: #111;
	}

	.scrub-btn:disabled {
		opacity: 0.3;
		cursor: not-allowed;
	}

	.scrub-btn.play {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: #fff;
		font-size: 0.7rem;
	}

	.scrub-btn.play:hover:not(:disabled) {
		background: var(--color-accent-hover);
		border-color: var(--color-accent-hover);
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
		color: var(--color-text-muted);
	}

	.scrub-date {
		font-size: 0.95rem;
		font-weight: 800;
		color: var(--color-text);
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
		background: rgba(0, 0, 0, 0.12);
		pointer-events: none;
	}

	.tl-progress {
		position: absolute;
		left: 1.4rem;
		top: 1.35rem;
		height: 2px;
		transform: translateY(-50%);
		background: var(--color-accent);
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
		color: var(--color-text-dim);
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
		background: rgba(0, 0, 0, 0.18);
		border: 1px solid rgba(0, 0, 0, 0.12);
		transition:
			background 0.1s,
			transform 0.1s;
	}

	.tl-tick:hover::after {
		background: rgba(0, 0, 0, 0.4);
		transform: scale(1.5);
	}

	.tl-tick.active::after {
		width: 0.7rem;
		height: 0.7rem;
		background: var(--color-accent);
		border-color: var(--color-accent);
		box-shadow: 0 0 0 3px var(--color-accent-border);
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
		color: var(--color-text-muted);
	}

	.tt-caption {
		font-size: 0.58rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: #627174;
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
		background: rgba(31, 43, 52, 0.06);
	}

	.tt-bar-fill {
		width: 100%;
		border-radius: 0.2rem;
		box-shadow: inset 0 0 0 1px rgba(31, 43, 52, 0.18);
	}

	.tt-val {
		font-size: 0.62rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: #46555c;
	}

	.tt-lbl {
		font-size: 0.56rem;
		font-weight: 600;
		color: #627174;
		white-space: nowrap;
	}

	.tt-col.active .tt-val {
		color: #18252b;
	}

	.tt-col.active .tt-bar-track {
		box-shadow: inset 0 0 0 1.5px var(--color-accent);
	}

	.control-rail {
		flex: none;
		width: 14rem;
		z-index: 3;
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 0.9rem;
		background: var(--color-surface);
		border-right: 1px solid var(--color-border);
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
		color: var(--color-text-muted);
	}

	.rail-def-region {
		display: block;
		font-size: 0.72rem;
		font-weight: 800;
		color: var(--color-text);
		margin-bottom: 0.15rem;
	}

	.rail-title {
		font-size: 0.62rem;
		font-weight: 800;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: var(--color-text-muted);
	}

	.rail-collapse,
	.rail-reopen {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.6rem;
		height: 1.6rem;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: #fff;
		color: #444;
		font-size: 0.85rem;
		line-height: 1;
		cursor: pointer;
		transition:
			background 0.12s,
			border-color 0.12s,
			color 0.12s;
	}

	.rail-collapse:hover,
	.rail-reopen:hover {
		background: var(--color-surface-muted);
		border-color: var(--color-text-dim);
		color: #111;
	}

	.rail-reopen {
		position: absolute;
		top: 0.8rem;
		left: 0.8rem;
		z-index: 3;
		width: 2rem;
		height: 2rem;
		background: rgba(255, 255, 255, 0.9);
		box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
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
		border-top: 1px solid var(--color-border);
	}

	.rail-label {
		font-size: 0.58rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--color-text-muted);
	}

	.mode-toggle {
		display: flex;
		gap: 0.3rem;
	}

	.mode-toggle button {
		flex: 1;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: #fff;
		color: var(--color-text-muted);
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
		color: var(--color-text);
		border-color: var(--color-text-dim);
	}

	.mode-toggle button.active {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: #fff;
	}

	.week-buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
	}

	.week-btn {
		flex: 1 1 3.2rem;
		border: 1px solid var(--color-border);
		border-radius: 0.3rem;
		background: #fff;
		color: var(--color-text-muted);
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
		color: var(--color-text);
		border-color: var(--color-text-dim);
	}

	.week-btn.active {
		background: var(--color-accent);
		border-color: var(--color-accent);
		color: #fff;
	}

	.rail-select {
		width: 100%;
		border: 1px solid var(--color-border);
		border-radius: 0.35rem;
		background: #fff;
		color: var(--color-text);
		font-size: 0.72rem;
		font-weight: 600;
		padding: 0.35rem 0.4rem;
		cursor: pointer;
	}

	.rail-select:hover {
		border-color: var(--color-text-dim);
	}

	.rail-select option {
		color: #111;
	}

	.legend-caption {
		font-size: 0.67rem;
		font-weight: 600;
		line-height: 1.4;
		color: var(--color-text-muted);
		margin: 0 0 0.5rem;
	}

	.window-swatches {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 0.6rem;
	}

	.passed-note {
		margin-top: 0.55rem;
	}

	.legend-note {
		margin: 0.35rem 0 0;
		font-size: 0.62rem;
		line-height: 1.4;
		color: var(--color-text-muted);
	}

	.swatch-item {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.66rem;
		font-weight: 700;
		color: var(--color-text);
		white-space: nowrap;
	}

	.swatch {
		width: 0.8rem;
		height: 0.8rem;
		border-radius: 0.2rem;
		box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.15);
		flex: none;
	}

	.legend-bar {
		height: 0.4rem;
		border-radius: 999px;
		box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.15);
	}

	.legend-ticks {
		display: flex;
		justify-content: space-between;
		margin-top: 0.2rem;
	}

	.legend-ticks span {
		width: 1px;
		height: 0.28rem;
		background: var(--color-text-dim);
	}

	.legend-labels {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-top: 0.12rem;
		font-size: 0.72rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: var(--color-text);
	}
</style>
