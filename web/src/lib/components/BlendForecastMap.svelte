<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import '$lib/maplibre-worker';
	import {
		DEFAULT_BLEND_FORECAST_VIEW,
		getBlendForecast,
		type BlendForecastData,
		type BlendForecastModel,
		type BlendForecastPoint,
		type BlendForecastResolution,
		type BlendForecastView
	} from '$lib/api';
	import { getRegionBoundary } from '$lib/api/regions';
	import {
		probGradient,
		windowColor,
		windowGradient,
		ONSET_PASSED_COLOR,
		rampColor,
		consensusOnsetDay,
		onsetHasPassed,
		argmax,
		binDateLabel,
		binsFor,
		probScaleMax,
		fmtProb,
		fmtDate,
		monthLabel
	} from '$lib/onset';
	import CellInspector from './CellInspector.svelte';
	import MapTooltip from './MapTooltip.svelte';
	import { BASEMAP_STYLES, isDarkBasemap, type BasemapStyleId } from '$lib/basemaps';
	import { formatLatLon } from '$lib/geo';
	import { latestIssueDate, formatIssueDate } from '$lib/forecast-freshness';
	import {
		buildAdm3ForecastGeoJson,
		usesNamedAreas,
		type ForecastFeatureCollection
	} from './blend-map/adm3';
	import { BoundaryLayers } from '$lib/components/metric-map/boundaries.svelte';
	import { BOUNDARY_LEVELS } from '$lib/components/metric-map/constants';
	import type { BoundaryLevel, BoundaryStyleDef } from '$lib/components/metric-map/types';

	type Props = { jobId: string; regionId?: string | null };
	let { jobId, regionId = null }: Props = $props();

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

	// One payload per (model, resolution) view, fetched on first selection: the
	// daily payload is several times the weekly one, so it never loads up front.
	let payloads = $state.raw<Record<string, BlendForecastData>>({});
	let viewErrors = $state.raw<Record<string, string>>({});
	const requestedViews = new Set<string>();

	let resolution = $state<BlendForecastResolution>('weekly');
	// Only the day-level blend has daily probabilities; the weekly view offers both.
	let weeklyModel = $state<BlendForecastModel>('weekly_model');
	const view = $derived<BlendForecastView>(
		resolution === 'daily'
			? { model: 'daily_model', resolution: 'daily' }
			: { model: weeklyModel, resolution: 'weekly' }
	);
	const data = $derived(payloads[viewKey(view)] ?? null);
	const error = $derived(viewErrors[viewKey(view)] ?? null);
	const loading = $derived(!data && !error);
	const bins = $derived(binsFor(view.resolution));

	// Read from the default payload, which every forecast has and loads first.
	const availableViews = $derived(
		payloads[viewKey(DEFAULT_BLEND_FORECAST_VIEW)]?.available_views ?? []
	);
	const offersDailyView = $derived(availableViews.some((v) => v.resolution === 'daily'));
	const offersModelChoice = $derived(
		availableViews.some((v) => v.resolution === 'weekly' && v.model === 'daily_model')
	);

	function viewKey(v: BlendForecastView): string {
		return `${v.model}.${v.resolution}`;
	}

	let adm3Boundaries = $state<GeoJSON.FeatureCollection | null>(null);
	let boundaryError = $state<string | null>(null);

	let selectedDate = $state('');
	// The highlighted onset bin, remembered per resolution so toggling keeps each.
	let selectedBins = $state<Record<BlendForecastResolution, number>>({ weekly: 0, daily: 0 });
	const selectedBin = $derived(selectedBins[view.resolution]);
	// 'window' colors by the selected window's probability (magnitude); 'expected'
	// collapses the distribution to each point's most-likely window (which window).
	let colorMode = $state<'window' | 'expected'>('window');

	// Which end of the ordinal ramp is the soonest window. 'yellow' (imminent =
	// hot) is our default; 'purple' matches the science team's static legend.
	let soonestColor = $state<'yellow' | 'purple'>('yellow');
	// The toggle flips the whole plasma direction: the vivid end marks both the
	// soonest window and the highest probability.
	const reversed = $derived(soonestColor === 'purple');

	// Per-cell estimated onset day (index-aligned to data.points), used to gray a
	// cell once the shown forecast was issued after onset likely occurred.
	const cellConsensus = $derived.by(() =>
		data ? data.points.map((pt) => consensusOnsetDay(bins, data!.issue_dates, pt.probs)) : []
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

	// Keyed rather than held, so the inspector follows the cell across views.
	let selectedCellKey = $state<string | null>(null);
	const selectedCell = $derived(
		selectedCellKey && data
			? (data.points.find((pt) => pointKey(pt) === selectedCellKey) ?? null)
			: null
	);

	function pointKey(pt: BlendForecastPoint): string {
		return pt.id ?? `${pt.lat}_${pt.lon}`;
	}

	// Subtler boundary styling than the benchmark map's: this map's bright plasma
	// cells are the focus, so thin translucent lines over a soft dark halo keep
	// the admin outlines legible without the heavy white halos flooding the grid.
	const FORECAST_BOUNDARY_STYLES: Record<BoundaryLevel, BoundaryStyleDef> = {
		adm1: {
			...BOUNDARY_LEVELS.adm1,
			strokeColor: 'rgba(236, 240, 245, 0.7)',
			haloColor: 'rgba(10, 14, 20, 0.55)',
			strokeWidth: 1.1,
			haloWidth: 2.4
		},
		adm2: {
			...BOUNDARY_LEVELS.adm2,
			strokeColor: 'rgba(236, 240, 245, 0.42)',
			haloColor: 'rgba(10, 14, 20, 0.4)',
			strokeWidth: 0.6,
			haloWidth: 1.5
		}
	};

	// Optional admin-boundary overlays, reusing the benchmark map's layer
	// manager. Keyed by the forecast's region id (from the parent), which the
	// blend-forecast payload itself doesn't carry.
	const boundaries = new BoundaryLayers(
		() => map,
		() => regionId ?? undefined,
		FORECAST_BOUNDARY_STYLES
	);
	const boundaryLevels = Object.keys(BOUNDARY_LEVELS) as BoundaryLevel[];

	const EMPTY_PROBS: number[] = [];

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
		bin: number,
		passed: boolean
	): { color: string; opacity: number } {
		// Onset already occurred by this issue date: the forward outlook is stale,
		// so drain the color to a dim gray rather than show a misleading dot.
		if (passed) return { color: ONSET_PASSED_COLOR, opacity: 0.45 };
		if (colorMode === 'expected') {
			const w = argmax(row);
			// Fainter where the timing is uncertain — a weak plurality reads as
			// "we don't really know when," with a visible floor so no dot vanishes.
			const certainty = Math.min(1, (row[w] ?? 0) / scaleMax);
			return { color: windowColor(bins, w, reversed), opacity: 0.4 + 0.55 * certainty };
		}
		return { color: rampColor((row[bin] ?? 0) / scaleMax, reversed), opacity: 0.9 };
	}

	function stylePoint(
		d: BlendForecastData,
		pt: BlendForecastPoint,
		idx: number,
		date: string,
		bin: number
	) {
		const dateIdx = d.issue_dates.indexOf(date);
		const row = dateIdx >= 0 ? (pt.probs[dateIdx] ?? EMPTY_PROBS) : EMPTY_PROBS;
		const passed = dateIdx >= 0 && onsetHasPassed(date, cellConsensus[idx] ?? null);
		return featureStyle(row, bin, passed);
	}

	function buildPointGeoJson(
		d: BlendForecastData,
		date: string,
		bin: number
	): ForecastFeatureCollection {
		const dateIdx = d.issue_dates.indexOf(date);
		const hx = gridStep.dx / 2;
		const hy = gridStep.dy / 2;
		return {
			type: 'FeatureCollection' as const,
			features: d.points.map((pt, i) => {
				const row = dateIdx >= 0 ? (pt.probs[dateIdx] ?? EMPTY_PROBS) : EMPTY_PROBS;
				const passed = dateIdx >= 0 && onsetHasPassed(date, cellConsensus[i] ?? null);
				const { color, opacity } = featureStyle(row, bin, passed);
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

	function buildGeoJson(
		d: BlendForecastData,
		date: string,
		bin: number
	): ForecastFeatureCollection {
		if (adm3Boundaries && usesAdm3Polygons(d)) {
			const polygonGeojson = buildAdm3ForecastGeoJson(d, adm3Boundaries, (pt, idx) =>
				stylePoint(d, pt, idx, date, bin)
			);
			if (polygonGeojson) return polygonGeojson;
		}
		return buildPointGeoJson(d, date, bin);
	}

	function updateSource() {
		if (!map || !data || !selectedDate) return;
		const src = map.getSource('blend') as maplibregl.GeoJSONSource | undefined;
		if (src) src.setData(buildGeoJson(data, selectedDate, selectedBin));
		else initLayer(data);
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
		const geojson = buildGeoJson(d, selectedDate, selectedBin);
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
		map.addLayer({
			id: 'blend-outlines',
			type: 'line',
			source: 'blend',
			paint: {
				'line-color': dotStroke(),
				'line-opacity': 0.7,
				'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.45, 7, 1.2]
			}
		});
		if (fit) fitToData(d);
	}

	function usesAdm3Polygons(d: BlendForecastData): boolean {
		return d.region_id === 'ethiopia' && usesNamedAreas(d.points);
	}

	function isFeatureCollection(value: unknown): value is GeoJSON.FeatureCollection {
		return (
			typeof value === 'object' &&
			value != null &&
			(value as { type?: unknown }).type === 'FeatureCollection' &&
			Array.isArray((value as { features?: unknown }).features)
		);
	}

	async function loadAdm3Boundaries(d: BlendForecastData) {
		if (!usesAdm3Polygons(d)) return;
		const regionId = d.region_id;
		if (!regionId) return;
		try {
			const { geojson } = await getRegionBoundary(regionId, 'adm3');
			if (!isFeatureCollection(geojson)) throw new Error('ADM3 boundary response was not GeoJSON');
			const joined = buildAdm3ForecastGeoJson(d, geojson, (pt, idx) =>
				stylePoint(d, pt, idx, selectedDate, selectedBin)
			);
			if (!joined) throw new Error('ADM3 boundaries did not match forecast areas');
			adm3Boundaries = geojson;
			boundaryError = null;
			// The view may have changed while the boundaries loaded; draw the current one.
			if (mapReady && data) {
				initLayer(data, { fit: false });
				fitToData(data);
			}
		} catch (e) {
			boundaryError = e instanceof Error ? e.message : 'Failed to load ADM3 boundaries';
			adm3Boundaries = null;
			if (mapReady && data) initLayer(data, { fit: false });
		}
	}

	function extendBoundsWithCoordinates(bounds: maplibregl.LngLatBounds, coordinates: unknown) {
		if (!Array.isArray(coordinates)) return;
		if (
			coordinates.length >= 2 &&
			typeof coordinates[0] === 'number' &&
			typeof coordinates[1] === 'number'
		) {
			bounds.extend([coordinates[0], coordinates[1]]);
			return;
		}
		for (const child of coordinates) extendBoundsWithCoordinates(bounds, child);
	}

	// Frame the map on the region's grid points, leaving room for the left rail
	// and bottom scrubber so no dots hide behind the chrome.
	function fitToData(d: BlendForecastData) {
		if (!map || !d.points.length) return;
		const bounds = new maplibregl.LngLatBounds();
		const geojson = buildGeoJson(d, selectedDate, selectedBin);
		if (geojson.features.some((feature) => feature.geometry.type !== 'Point')) {
			for (const feature of geojson.features)
				extendBoundsWithCoordinates(bounds, feature.geometry.coordinates);
		} else {
			for (const pt of d.points) bounds.extend([pt.lon, pt.lat]);
		}
		map.fitBounds(bounds, {
			padding: { top: 48, right: 48, bottom: 80, left: collapsed ? 48 : 240 },
			maxZoom: 6,
			duration: 0
		});
	}

	const BLEND_LAYERS = ['blend-cells', 'blend-outlines'];

	function setCellsVisible(visible: boolean) {
		if (!map) return;
		for (const id of BLEND_LAYERS) {
			if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
		}
	}

	// Hide the previous view's cells until the selected view's payload arrives,
	// so the map never disagrees with the legend and controls.
	$effect(() => {
		if (mapReady) setCellsVisible(data != null);
	});

	$effect(() => {
		if (mapReady && data && selectedDate) {
			selectedBin; // track
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
			// setStyle wipes every custom layer, so drop the manager's stale layer
			// state and re-add the visible boundary levels on top of the cells.
			boundaries.clearFromMap();
			boundaries.reloadVisible();
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

	const dataThrough = $derived(data ? latestIssueDate(data.issue_dates) : null);
	const dateIndex = $derived(data ? data.issue_dates.indexOf(selectedDate) : -1);
	const dateCount = $derived(data?.issue_dates.length ?? 0);

	// Top of the colour ramp for the shown issue date: 100% for weeks, the peak
	// daily probability for days (see probScaleMax). Expected-onset mode scales
	// its certainty fade by the same peak.
	const scaleMax = $derived(
		data && dateIndex >= 0
			? probScaleMax(
					bins,
					data.points.map((pt) => pt.probs[dateIndex] ?? EMPTY_PROBS),
					colorMode === 'window' ? selectedBin : undefined
				)
			: 1
	);

	function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
		const { [key]: _removed, ...rest } = record;
		return rest;
	}

	async function loadView(v: BlendForecastView) {
		const key = viewKey(v);
		if (requestedViews.has(key)) return;
		requestedViews.add(key);
		viewErrors = withoutKey(viewErrors, key);
		try {
			const payload = await getBlendForecast(jobId, v);
			payloads = { ...payloads, [key]: payload };
		} catch (e) {
			const message = e instanceof Error ? e.message : 'Failed to load blend forecast';
			viewErrors = { ...viewErrors, [key]: message };
			// Selecting this view again retries it.
			requestedViews.delete(key);
		}
	}

	$effect(() => {
		const v = view;
		// Track only the selected view; loadView reads and writes the error map.
		untrack(() => void loadView(v));
	});

	// Views share issue dates, but keep the selection valid if one ever differs.
	$effect(() => {
		if (data && !data.issue_dates.includes(selectedDate)) selectedDate = data.issue_dates[0] ?? '';
	});

	let adm3Requested = false;
	$effect(() => {
		if (!data || adm3Requested) return;
		adm3Requested = true;
		void loadAdm3Boundaries(data);
	});

	function selectBin(idx: number) {
		selectedBins = { ...selectedBins, [view.resolution]: idx };
	}

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
			const unit = view.resolution === 'daily' ? 'day' : 'window';
			return `Most likely onset ${unit} per location. Fainter dots mean the timing is less certain.`;
		}
		const thr = data?.onset_threshold;
		const onset = thr != null ? `monsoon onset (rainfall ≥ ${thr} mm)` : 'monsoon onset';
		return `Chance ${onset} begins ${binPhrase(selectedBin)}.`;
	});

	// "in Week 2 (Jun 9–Jun 15)", "on Jun 12", or "after Jun 29" for the shown forecast.
	function binPhrase(idx: number): string {
		if (!selectedDate) return `in ${bins.labels[idx]}`;
		const dates = binDateLabel(bins, selectedDate, idx);
		if (idx === bins.laterIndex) return dates;
		return view.resolution === 'daily' ? `on ${dates}` : `in ${bins.labels[idx]} (${dates})`;
	}

	// The day selector's readout: the calendar date, with its lead day for context.
	function dayBinLabel(idx: number): string {
		if (!selectedDate) return bins.labels[idx];
		if (idx === bins.laterIndex) return `Later (${binDateLabel(bins, selectedDate, idx)})`;
		return `${binDateLabel(bins, selectedDate, idx)} · ${bins.labels[idx]}`;
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

	function featurePoint(point: maplibregl.PointLike): BlendForecastPoint | null {
		if (!map || !data) return null;
		const features = map.queryRenderedFeatures(point, {
			layers: ['blend-cells'].filter((id) => map?.getLayer(id))
		});
		const idx = Number(features[0]?.properties?.idx);
		return Number.isInteger(idx) ? (data.points[idx] ?? null) : null;
	}

	function selectFeatureCell(e: maplibregl.MapMouseEvent) {
		const pt = featurePoint(e.point);
		if (pt) selectedCellKey = pointKey(pt);
	}

	onMount(() => {
		if (!mapHost) return;
		map = new maplibregl.Map({
			container: mapHost,
			style: basemapStyle().url,
			center: [20, 10],
			zoom: 1.8,
			attributionControl: false
		});
		map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
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
			const pt = featurePoint(e.point) ?? nearestPoint(e.lngLat.lng, e.lngLat.lat);
			const dateIdx = data ? data.issue_dates.indexOf(selectedDate) : -1;
			tooltipProbs = pt && dateIdx >= 0 ? (pt.probs[dateIdx] ?? null) : null;
		});
		map.on('mouseout', () => {
			tooltipVisible = false;
			tooltipProbs = null;
		});

		// Click a grid cell to open its season inspector.
		map.on('click', 'blend-cells', selectFeatureCell);
		map.on('mouseenter', 'blend-cells', () => {
			if (map) map.getCanvas().style.cursor = 'pointer';
		});
		map.on('mouseleave', 'blend-cells', () => {
			if (map) map.getCanvas().style.cursor = '';
		});
	});

	onDestroy(() => {
		stopPlay();
		resizeObserver?.disconnect();
		map?.remove();
		map = null;
	});
</script>

<div class="blend-map-wrap" class:fullscreen>
	<aside class="control-rail" class:collapsed data-tour="forecast-controls">
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
			{#if dataThrough}
				<p class="rail-def">Data through {formatIssueDate(dataThrough)}</p>
			{/if}
		</div>

		{#if offersDailyView}
			<div class="rail-group">
				<span class="rail-label">Onset timing</span>
				<div class="mode-toggle">
					<button class:active={resolution === 'weekly'} onclick={() => (resolution = 'weekly')}>
						By week
					</button>
					<button class:active={resolution === 'daily'} onclick={() => (resolution = 'daily')}>
						By day
					</button>
				</div>
			</div>
		{/if}

		{#if offersModelChoice && resolution === 'weekly'}
			<div class="rail-group">
				<span class="rail-label">Blend</span>
				<div class="mode-toggle">
					<button
						class:active={weeklyModel === 'weekly_model'}
						onclick={() => (weeklyModel = 'weekly_model')}
					>
						Week-level blend
					</button>
					<button
						class:active={weeklyModel === 'daily_model'}
						onclick={() => (weeklyModel = 'daily_model')}
					>
						Day-level blend
					</button>
				</div>
			</div>
		{/if}

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

		{#if colorMode === 'window' && view.resolution === 'weekly'}
			<div class="rail-group">
				<span class="rail-label">Onset window</span>
				<div class="week-buttons">
					{#each bins.labels as label, i (label)}
						<button class="week-btn" class:active={i === selectedBin} onclick={() => selectBin(i)}>
							{label}
						</button>
					{/each}
				</div>
			</div>
		{:else if colorMode === 'window'}
			<div class="rail-group">
				<label class="rail-label" for="onset-day">Onset day</label>
				<span class="day-readout">{dayBinLabel(selectedBin)}</span>
				<input
					id="onset-day"
					class="day-slider"
					type="range"
					min="0"
					max={bins.laterIndex}
					step="1"
					value={selectedBin}
					aria-valuetext={dayBinLabel(selectedBin)}
					oninput={(e) => selectBin(Number(e.currentTarget.value))}
				/>
				<div class="day-slider-ends">
					<span>{selectedDate ? binDateLabel(bins, selectedDate, 0) : bins.labels[0]}</span>
					<span>Later</span>
				</div>
			</div>
		{/if}

		<div class="rail-group">
			<span class="rail-label" id="basemap-label">Base map</span>
			<select class="rail-select" aria-labelledby="basemap-label" bind:value={selectedBasemap}>
				{#each BASEMAP_STYLES as style (style.id)}
					<option value={style.id}>{style.label}</option>
				{/each}
			</select>
		</div>

		{#if regionId}
			<div class="rail-group">
				<span class="rail-label">Admin boundaries</span>
				<div class="mode-toggle">
					{#each boundaryLevels as level (level)}
						<button
							class:active={boundaries.visibleLevels.has(level)}
							disabled={boundaries.loading.has(level)}
							onclick={() => boundaries.toggle(level)}
						>
							{BOUNDARY_LEVELS[level].label}
						</button>
					{/each}
				</div>
				{#each boundaryLevels as level (level)}
					{#if boundaries.errors[level]}
						<p class="boundary-error">{boundaries.errors[level]}</p>
					{/if}
				{/each}
			</div>
		{/if}

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
					<span>{fmtProb(scaleMax / 2)}</span>
					<span>{fmtProb(scaleMax)}</span>
				</div>
				{#if scaleMax < 1}
					<p class="legend-note">
						Daily chances are small, so the scale tops out at this forecast's highest daily chance.
					</p>
				{/if}
			{:else if view.resolution === 'daily'}
				<div class="legend-bar" style="background: {windowGradient(reversed)}"></div>
				<div class="legend-labels">
					<span>{selectedDate ? binDateLabel(bins, selectedDate, 0) : bins.labels[0]}</span>
					<span>Later</span>
				</div>
			{:else}
				<div class="window-swatches">
					{#each bins.labels as label, i (label)}
						<div class="swatch-item">
							<span class="swatch" style="background: {windowColor(bins, i, reversed)}"></span>
							<span>{label}</span>
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
			{#if boundaries.visibleLayers.length > 0}
				<p class="legend-note">
					Boundaries: geoBoundaries ({boundaries.visibleLayers.map((l) => l.label).join('; ')})
				</p>
			{/if}
		</div>
	</aside>

	<div class="map-area" data-tour="forecast-map">
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
				{bins}
				issueDates={data.issue_dates}
				regionName={data.region_name}
				{selectedDate}
				{soonestColor}
				onClose={() => (selectedCellKey = null)}
			/>
		{/if}

		{#if loading}
			<div class="overlay muted">Loading blend forecast…</div>
		{:else if error}
			<div class="overlay error">{error}</div>
		{:else if !data?.points.length}
			<div class="overlay muted">No blend forecast data for this job.</div>
		{/if}

		{#if boundaryError && data && usesAdm3Polygons(data)}
			<div class="boundary-note">ADM3 boundaries unavailable; showing centroids.</div>
		{/if}

		{#if tooltipVisible && !loading}
			<MapTooltip x={tooltipX} y={tooltipY} coords={formatLatLon(tooltipLat, tooltipLon)}>
				{#if tooltipProbs && view.resolution === 'daily'}
					{@const probs = tooltipProbs}
					{@const peak = argmax(probs)}
					{@const top = Math.max(0.01, probs[peak] ?? 0)}
					<span class="tt-caption">Monsoon onset timing</span>
					<div class="tt-days">
						{#each bins.labels as label, i (label)}
							<div
								class="tt-day"
								class:active={colorMode === 'window' && i === selectedBin}
								class:later={i === bins.laterIndex}
								title="{dayBinLabel(i)}: {fmtProb(probs[i] ?? 0)}"
							>
								<div
									class="tt-bar-fill"
									style="height: {Math.max(
										3,
										((probs[i] ?? 0) / top) * 100
									)}%; background: {windowColor(bins, i, reversed)}"
								></div>
							</div>
						{/each}
					</div>
					<span class="tt-summary"
						>Most likely {dayBinLabel(peak)} · {fmtProb(probs[peak] ?? 0)}</span
					>
					{#if colorMode === 'window' && selectedBin !== peak}
						<span class="tt-summary">
							{dayBinLabel(selectedBin)} · {fmtProb(probs[selectedBin] ?? 0)}
						</span>
					{/if}
				{:else if tooltipProbs}
					<span class="tt-caption">Monsoon onset timing</span>
					<div class="tt-spark">
						{#each bins.labels as label, i (label)}
							<div class="tt-col" class:active={colorMode === 'window' && i === selectedBin}>
								<div class="tt-bar-track">
									<div
										class="tt-bar-fill"
										style="height: {Math.max(
											3,
											(tooltipProbs[i] ?? 0) * 100
										)}%; background: {windowColor(bins, i, reversed)}"
									></div>
								</div>
								<span class="tt-val">{fmtProb(tooltipProbs[i] ?? 0)}</span>
								<span class="tt-lbl">{label}</span>
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
					<div
						class="tl-progress"
						style="width: {tlWidth(Math.max(0, dateIndex), dateCount)}"
					></div>
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

	/* Keep the map credit above the date scrubber, which spans the bottom edge. */
	.map-area:has(.scrubber) :global(.maplibregl-ctrl-bottom-right) {
		bottom: 3.4rem;
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

	.boundary-note {
		position: absolute;
		top: 0.7rem;
		left: 50%;
		z-index: 3;
		transform: translateX(-50%);
		max-width: min(90%, 28rem);
		padding: 0.45rem 0.7rem;
		border: 1px solid rgba(31, 43, 52, 0.14);
		border-radius: 0.35rem;
		background: rgba(255, 255, 255, 0.92);
		box-shadow: 0 0.35rem 1.1rem rgba(3, 14, 25, 0.16);
		color: #35424a;
		font-size: 0.68rem;
		font-weight: 700;
		pointer-events: none;
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

	.tt-days {
		display: flex;
		align-items: flex-end;
		gap: 0.08rem;
		width: 14rem;
		height: 2.6rem;
	}

	.tt-day {
		flex: 1;
		height: 100%;
		display: flex;
		align-items: flex-end;
		border-radius: 0.1rem;
		background: rgba(31, 43, 52, 0.06);
	}

	/* "Later" is open-ended, not one more day: set it apart from the run of days. */
	.tt-day.later {
		flex: 2;
		margin-left: 0.25rem;
	}

	.tt-day.active {
		box-shadow: 0 0 0 1.5px var(--color-accent);
	}

	.tt-summary {
		font-size: 0.62rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: #46555c;
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

	.day-readout {
		font-size: 0.78rem;
		font-weight: 800;
		font-variant-numeric: tabular-nums;
		color: var(--color-text);
	}

	.day-slider {
		width: 100%;
		margin: 0;
		accent-color: var(--color-accent);
		cursor: pointer;
	}

	.day-slider-ends {
		display: flex;
		justify-content: space-between;
		font-size: 0.62rem;
		font-weight: 600;
		color: var(--color-text-muted);
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

	.boundary-error {
		margin: 0.35rem 0 0;
		font-size: 0.62rem;
		line-height: 1.4;
		color: var(--color-danger);
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
