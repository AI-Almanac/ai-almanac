<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import {
		getRegionBoundary,
		type JobGridResponse,
		type Job,
		type JobCellResponse
	} from '$lib/api';
	import { getCachedJobGrid, getCachedJobCell } from '$lib/benchmarks.svelte';
	import GridCellInspector from '$lib/components/GridCellInspector.svelte';

	type MetricDef = { value: string; label: string };

	// One RunDef per complete job (one model per job)
	type RunDef = {
		jobId: string;
		modelName: string;
		colorIndex: number;
	};

	type Props = {
		jobs: Job[]; // complete jobs in the selected run group
		forecastWindow: string;
		metrics: MetricDef[];
	};
	let { jobs, forecastWindow, metrics }: Props = $props();

	type GridFeature = {
		type: 'Feature';
		properties: {
			color: string;
			lat: number;
			lon: number;
			displayVal: string;
		};
		geometry: {
			type: 'Polygon';
			coordinates: number[][][];
		};
	};
	type GridFeatureCollection = {
		type: 'FeatureCollection';
		features: GridFeature[];
	};

	let mapContainer = $state<HTMLElement | null>(null);
	let map = $state<maplibregl.Map | null>(null);
	let mapReady = $state(false);

	// ColorBrewer sequential scales for climatology raw values: [metric][colorIndex] low→high
	const COLOR_SCALES: Record<string, string[][]> = {
		false_alarm_rate: [
			['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026'], // YlOrRd
			['#feebe2', '#fbb4b9', '#f768a1', '#c51b8a', '#7a0177'], // RdPu
			['#fff5eb', '#fdd0a2', '#fdae6b', '#e6550d', '#a63603'], // Oranges
			['#f2f0f7', '#cbc9e2', '#9e9ac8', '#756bb1', '#54278f'] // Purples
		],
		miss_rate: [
			['#eff3ff', '#bdd7e7', '#6baed6', '#2171b5', '#084594'], // Blues
			['#edf8fb', '#b2e2e2', '#66c2a4', '#2ca25f', '#006d2c'], // BuGn
			['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'], // Greens
			['#fff7fb', '#ece2f0', '#a6bddb', '#1c9099', '#016450'] // GnBu-ish
		],
		mean_mae: [
			['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'], // Greens
			['#fcfbfd', '#dadaeb', '#9e9ac8', '#756bb1', '#54278f'], // Purples
			['#fff5f0', '#fdd0a2', '#fc8d59', '#d7301f', '#7f0000'], // Reds
			['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b'] // Blues-dark
		],
		rmse: [
			['#ffffcc', '#c2e699', '#78c679', '#31a354', '#006837'],
			['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'],
			['#fff5f0', '#fdd0a2', '#fc8d59', '#d7301f', '#7f0000'],
			['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b']
		],
		mae: [
			['#ffffcc', '#c2e699', '#78c679', '#31a354', '#006837'],
			['#fff5f0', '#fdd0a2', '#fc8d59', '#d7301f', '#7f0000'],
			['#fff7fb', '#ece2f0', '#a6bddb', '#1c9099', '#016450'],
			['#fcfbfd', '#dadaeb', '#9e9ac8', '#756bb1', '#54278f']
		],
		acc: [
			['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b'],
			['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'],
			['#f7f7f7', '#cccccc', '#969696', '#525252', '#252525'],
			['#f7fcfd', '#ccece6', '#66c2a4', '#238b45', '#005824']
		]
	};
	const FALLBACK_SCALE = ['#f7f7f7', '#cccccc', '#969696', '#525252', '#252525'];

	// Diverging scale for anomaly (model − climatology): blue → white → red
	// Blue = model better than baseline, red = model worse
	const DIVERGING_STOPS = ['#2166ac', '#92c5de', '#f7f7f7', '#f4a582', '#b2182b'];

	function getStops(metricValue: string, colorIndex: number): string[] {
		if (metricValue === 'bias') return DIVERGING_STOPS;
		const scales = COLOR_SCALES[metricValue];
		if (!scales) return FALLBACK_SCALE;
		return scales[colorIndex % scales.length];
	}

	function isHigherBetterMetric(metricValue: string): boolean {
		return metricValue === 'acc';
	}

	function isNeutralDeltaMetric(metricValue: string): boolean {
		return metricValue === 'bias';
	}

	type LayerState = {
		layerId: string;
		sourceId: string;
		data: JobGridResponse;
		geojson: GridFeatureCollection;
		bounds: maplibregl.LngLatBoundsLike | null;
		stops: string[];
		isDelta: boolean;
		deltaMaxAbs?: number;
		referenceData?: JobGridResponse; // present on delta layers for tooltip computation
		referenceModelName?: string;
	};

	type BoundaryLevel = 'adm1' | 'adm2';
	type BoundaryLayerState = {
		layerId: string;
		sourceId: string;
		label: string;
		source: string;
		geojson: unknown;
	};
	type BoundaryCacheEntry = {
		label: string;
		source: string;
		geojson: unknown;
	};
	type BoundaryMetadata = Awaited<ReturnType<typeof getRegionBoundary>>['metadata'];
	type BoundaryStyleDef = {
		label: string;
		type: string;
		strokeColor: string;
		haloColor: string;
		strokeWidth: number;
		haloWidth: number;
		zIndex: number;
	};

	const BOUNDARY_LEVELS: Record<BoundaryLevel, BoundaryStyleDef> = {
		adm1: {
			label: 'Admin 1',
			type: 'ADM1',
			strokeColor: 'rgba(25, 35, 52, 0.96)',
			haloColor: 'rgba(255, 255, 255, 0.9)',
			strokeWidth: 2.2,
			haloWidth: 4.6,
			zIndex: 38
		},
		adm2: {
			label: 'Admin 2',
			type: 'ADM2',
			strokeColor: 'rgba(67, 82, 103, 0.78)',
			haloColor: 'rgba(255, 255, 255, 0.72)',
			strokeWidth: 1.5,
			haloWidth: 2.2,
			zIndex: 36
		}
	};
	const boundaryCache = new globalThis.Map<string, BoundaryCacheEntry>();

	let layers = $state<Record<string, LayerState>>({});
	let boundaryLayers = $state<Record<BoundaryLevel, BoundaryLayerState | null>>({
		adm1: null,
		adm2: null
	});
	let boundaryLoading = $state<Set<BoundaryLevel>>(new Set());
	let boundaryErrors = $state<Partial<Record<BoundaryLevel, string>>>({});
	let visibleBoundaryLevels = $state<Set<BoundaryLevel>>(new Set());
	let loading = $state<Set<string>>(new Set());
	let errors = $state<Record<string, string>>({});
	let visibleKeys = $state<Set<string>>(new Set());
	let opacities = $state<Record<string, number>>({});
	let activeRuns = $state<RunDef[]>([]);
	type MapViewMode = 'single' | 'baseline' | 'difference' | 'swipe';
	let viewMode = $state<MapViewMode>('baseline');
	let selectedMetric = $state('');
	let selectedModelJobId = $state('');
	let selectedReferenceJobId = $state('climatology');
	let swipePosition = $state(50);
	let draggingSwipe = $state(false);
	let loadRequestId = 0;
	const boundaryRegion = $derived(jobs[0]?.region_id ?? jobs[0]?.params?.region);

	let panelCollapsed = $state(false);
	let fullscreen = $state(false);

	let tooltipVisible = $state(false);
	let tooltipX = $state(0);
	let tooltipY = $state(0);
	let tooltipContent = $state('');
	let selectedCell = $state<{ lat: number; lon: number } | null>(null);
	let cellResults = $state<JobCellResponse[]>([]);
	let cellLoading = $state(false);
	let cellError = $state<string | null>(null);
	let cellLoadRequestId = 0;

	// ---- Derived -----------------------------------------------------------------

	const jobIds = $derived(
		jobs
			.map((j) => j.id)
			.sort()
			.join(',')
	);

	const visibleLayers = $derived(
		[...visibleKeys].filter((k) => layers[k]).map((k) => ({ key: k, ...layers[k] }))
	);
	const visibleBoundaryLayers = $derived(
		[...visibleBoundaryLevels]
			.map((level) => boundaryLayers[level])
			.filter((layer) => layer != null)
	);
	const anyLoading = $derived(loading.size > 0);

	// ---- Key helpers -------------------------------------------------------------

	function rawLayerKey(jobId: string, modelName: string, metric: string) {
		return `raw||${jobId}||${modelName}||${metric}`;
	}

	function deltaLayerKey(
		jobId: string,
		modelName: string,
		metric: string,
		referenceJobId: string,
		referenceModelName: string
	) {
		return `delta||${jobId}||${modelName}||${metric}||${referenceJobId}||${referenceModelName}`;
	}

	function parseKey(key: string) {
		const [kind, jobId, modelName, metric, referenceJobId, referenceModelName] = key.split('||');
		return { kind, jobId, modelName, metric, referenceJobId, referenceModelName };
	}

	function metricLabel(metricValue: string) {
		return metrics.find((m) => m.value === metricValue)?.label ?? metricValue;
	}

	function modelDisplayName(modelName: string) {
		const labels: Record<string, string> = {
			fuxi: 'FuXi',
			aifs: 'AIFS',
			aifs_daily: 'AIFS Daily',
			fuxi_s2s: 'FuXi S2S',
			climatology: 'Climatology'
		};
		return labels[modelName.toLowerCase()] ?? modelName;
	}

	function modelRunLabel(run: RunDef) {
		return modelDisplayName(run.modelName);
	}

	function viewModeDescription(mode: MapViewMode) {
		if (mode === 'single') return 'Show raw metric values for one model.';
		if (mode === 'baseline') return 'Show where the selected model improves or worsens relative to climatology.';
		if (mode === 'difference') return 'Show the selected model minus another model or climatology.';
		return 'Compare two raw metric maps with a draggable split view.';
	}

	function availableModelRuns() {
		return activeRuns.filter((run) => run.modelName !== 'climatology');
	}

	function selectedModelRun() {
		return availableModelRuns().find((run) => run.jobId === selectedModelJobId) ?? availableModelRuns()[0];
	}

	function selectedReferenceRun() {
		if (selectedReferenceJobId === 'climatology') {
			return activeRuns.find((run) => run.modelName === 'climatology');
		}
		return availableModelRuns().find((run) => run.jobId === selectedReferenceJobId);
	}

	function sameRun(a: RunDef, b: RunDef) {
		return a.jobId === b.jobId && a.modelName === b.modelName;
	}

	function normalizeLensSelection() {
		const modelRuns = availableModelRuns();
		if (selectedMetric && !metrics.some((metric) => metric.value === selectedMetric)) {
			selectedMetric = metrics[0]?.value ?? '';
		}
		if (selectedModelJobId && !modelRuns.some((run) => run.jobId === selectedModelJobId)) {
			selectedModelJobId = modelRuns[0]?.jobId ?? '';
		}
		if (selectedReferenceJobId === selectedModelJobId) {
			const climatologyRun = activeRuns.find((run) => run.modelName === 'climatology');
			selectedReferenceJobId =
				climatologyRun?.jobId === selectedModelJobId
					? (modelRuns.find((run) => run.jobId !== selectedModelJobId)?.jobId ?? '')
					: 'climatology';
		}
	}

	function currentLensKey() {
		normalizeLensSelection();
		const modelRun = selectedModelRun();
		if (!modelRun || !selectedMetric) return null;
		if (viewMode === 'single') {
			return rawLayerKey(modelRun.jobId, modelRun.modelName, selectedMetric);
		}
		const referenceRun =
			viewMode === 'baseline'
				? activeRuns.find((run) => run.modelName === 'climatology')
				: selectedReferenceRun();
		if (!referenceRun || sameRun(referenceRun, modelRun)) {
			return rawLayerKey(modelRun.jobId, modelRun.modelName, selectedMetric);
		}
		return deltaLayerKey(
			modelRun.jobId,
			modelRun.modelName,
			selectedMetric,
			referenceRun.jobId,
			referenceRun.modelName
		);
	}

	function currentLensKeys() {
		normalizeLensSelection();
		if (viewMode !== 'swipe') {
			const key = currentLensKey();
			return key ? [key] : [];
		}
		const modelRun = selectedModelRun();
		const referenceRun = selectedReferenceRun();
		if (!modelRun || !referenceRun || !selectedMetric || sameRun(referenceRun, modelRun)) {
			const key = currentLensKey();
			return key ? [key] : [];
		}
		return [
			rawLayerKey(modelRun.jobId, modelRun.modelName, selectedMetric),
			rawLayerKey(referenceRun.jobId, referenceRun.modelName, selectedMetric)
		];
	}

	function swipeLongitude() {
		if (!map || !mapContainer) return null;
		const x = (mapContainer.clientWidth * swipePosition) / 100;
		return map.unproject([x, mapContainer.clientHeight / 2]).lng;
	}

	function setSwipePositionFromClientX(clientX: number) {
		if (!mapContainer) return;
		const rect = mapContainer.getBoundingClientRect();
		const pct = ((clientX - rect.left) / rect.width) * 100;
		swipePosition = Math.min(95, Math.max(5, pct));
	}

	function startSwipeDrag(event: PointerEvent) {
		event.preventDefault();
		event.stopPropagation();
		draggingSwipe = true;
		setSwipePositionFromClientX(event.clientX);
	}

	function moveSwipeWithKeyboard(event: KeyboardEvent) {
		const step = event.shiftKey ? 10 : 2;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			swipePosition = Math.max(5, swipePosition - step);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			swipePosition = Math.min(95, swipePosition + step);
		}
	}

	function updateSwipeFilters() {
		if (!map) return;
		const keys = currentLensKeys();
		for (const key of keys) {
			const state = layers[key];
			if (state && map.getLayer(state.layerId)) map.setFilter(state.layerId, ['all']);
		}
		if (viewMode !== 'swipe' || keys.length !== 2) return;
		const splitLon = swipeLongitude();
		if (splitLon == null) return;
		const [leftKey, rightKey] = keys;
		const leftLayer = layers[leftKey]?.layerId;
		const rightLayer = layers[rightKey]?.layerId;
		if (leftLayer && map.getLayer(leftLayer)) map.setFilter(leftLayer, ['<=', ['get', 'lon'], splitLon]);
		if (rightLayer && map.getLayer(rightLayer)) map.setFilter(rightLayer, ['>', ['get', 'lon'], splitLon]);
	}

	function applyLensSelection(fit = false) {
		const keys = currentLensKeys();
		const nextVisibleKeys = new Set(keys.filter((key) => layers[key]));
		for (const [layerKey, state] of Object.entries(layers)) {
			if (!map?.getLayer(state.layerId)) continue;
			map.setLayoutProperty(state.layerId, 'visibility', nextVisibleKeys.has(layerKey) ? 'visible' : 'none');
			if (nextVisibleKeys.has(layerKey)) {
				map.setPaintProperty(state.layerId, 'fill-opacity', opacities[layerKey] ?? 1);
			}
		}
		visibleKeys = nextVisibleKeys;
		updateSwipeFilters();
		const firstKey = keys.find((key) => layers[key]);
		if (fit && firstKey) fitToLayer(layers[firstKey]);
	}

	async function loadCellResults(lat: number, lon: number, window: string, jobsSnapshot: Job[]) {
		const requestId = ++cellLoadRequestId;
		cellResults = [];
		cellError = null;
		cellLoading = true;
		try {
			const results = await Promise.all(
				jobsSnapshot.map((job) => getCachedJobCell(job.id, job.model_name, window, lat, lon))
			);
			if (requestId !== cellLoadRequestId) return;
			cellResults = results;
		} catch (e) {
			if (requestId !== cellLoadRequestId) return;
			cellError = e instanceof Error ? e.message : 'Failed to load cell metrics';
		} finally {
			if (requestId !== cellLoadRequestId) return;
			cellLoading = false;
		}
	}

	function openCellInspector(lat: number, lon: number) {
		selectedCell = { lat, lon };
	}

	function closeCellInspector() {
		cellLoadRequestId++;
		selectedCell = null;
		cellResults = [];
		cellError = null;
		cellLoading = false;
	}

	// Find the value in a grid at the lat/lon closest to the given coordinates.
	function getValueAtLatLon(data: JobGridResponse, lat: number, lon: number): number | null {
		let bestI = 0,
			bestJ = 0,
			bestDist = Infinity;
		for (let i = 0; i < data.lats.length; i++) {
			for (let j = 0; j < data.lons.length; j++) {
				const d = Math.abs(data.lats[i] - lat) + Math.abs(data.lons[j] - lon);
				if (d < bestDist) {
					bestDist = d;
					bestI = i;
					bestJ = j;
				}
			}
		}
		return data.values[bestI]?.[bestJ] ?? null;
	}

	function buildTooltipContent(lat: number, lon: number): string {
		const header = `<strong>${lat.toFixed(2)}°N ${lon.toFixed(2)}°E</strong>`;
		if (visibleKeys.size === 0) return header;

		// Group visible layer keys by model name, preserving insertion order
		const byModelOrder: string[] = [];
		const byModel: Record<string, string[]> = {};
		for (const key of visibleKeys) {
			if (!layers[key]) continue;
			const { modelName } = parseKey(key);
			if (!byModel[modelName]) {
				byModel[modelName] = [];
				byModelOrder.push(modelName);
			}
			byModel[modelName].push(key);
		}

		const sections: string[] = [header];
		for (const modelName of byModelOrder) {
			const keys = byModel[modelName];
			const displayName = modelName === 'climatology' ? 'Climatology' : modelName.toUpperCase();
			const rows = keys.map((key) => {
				const ls = layers[key];
				const { metric } = parseKey(key);
				const val = getValueAtLatLon(ls.data, lat, lon);
				if (val == null) return `<span class="tt-metric">${metricLabel(metric)}: —</span>`;
				if (ls.isDelta && ls.referenceData) {
					const referenceVal = getValueAtLatLon(ls.referenceData, lat, lon);
					const delta = referenceVal != null ? val - referenceVal : null;
					const deltaStr =
						delta != null
							? ` <span class="tt-delta">(Δ${delta >= 0 ? '+' : ''}${delta.toFixed(3)})</span>`
							: '';
					return `<span class="tt-metric">${metricLabel(metric)}: ${val.toFixed(3)}${deltaStr}</span>`;
				}
				return `<span class="tt-metric">${metricLabel(metric)}: ${val.toFixed(3)}</span>`;
			});
			sections.push(
				`<div class="tt-group"><span class="tt-model">${displayName}</span>${rows.join('')}</div>`
			);
		}
		return sections.join('');
	}

	// ---- Color helpers -----------------------------------------------------------

	function lerpHex(a: string, b: string, t: number): string {
		const parse = (h: string) => [
			parseInt(h.slice(1, 3), 16),
			parseInt(h.slice(3, 5), 16),
			parseInt(h.slice(5, 7), 16)
		];
		const [ar, ag, ab] = parse(a);
		const [br, bg, bb] = parse(b);
		const r = Math.round(ar + (br - ar) * t);
		const g = Math.round(ag + (bg - ag) * t);
		const b2 = Math.round(ab + (bb - ab) * t);
		return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b2.toString(16).padStart(2, '0')}`;
	}

	function interpolateStops(stops: string[], t: number): string {
		if (stops.length === 0) return '#cccccc';
		if (t <= 0) return stops[0];
		if (t >= 1) return stops[stops.length - 1];
		const seg = t * (stops.length - 1);
		const lo = Math.floor(seg);
		const hi = Math.min(lo + 1, stops.length - 1);
		return lerpHex(stops[lo], stops[hi], seg - lo);
	}

	function mapLayerId(key: string) {
		return `metric-layer-${key}`;
	}

	function mapSourceId(key: string) {
		return `metric-source-${key}`;
	}

	function boundaryLayerId(level: BoundaryLevel) {
		return `boundary-layer-${level}`;
	}

	function boundarySourceId(level: BoundaryLevel) {
		return `boundary-source-${level}`;
	}

	function gridCellBounds(data: JobGridResponse) {
		const dlat = data.lats.length > 1 ? Math.abs(data.lats[1] - data.lats[0]) / 2 : 0.5;
		const dlon = data.lons.length > 1 ? Math.abs(data.lons[1] - data.lons[0]) / 2 : 0.5;
		return { dlat, dlon };
	}

	function boundsFromGrid(data: JobGridResponse): maplibregl.LngLatBoundsLike | null {
		if (data.lats.length === 0 || data.lons.length === 0) return null;
		const { dlat, dlon } = gridCellBounds(data);
		const west = Math.min(...data.lons) - dlon;
		const east = Math.max(...data.lons) + dlon;
		const south = Math.min(...data.lats) - dlat;
		const north = Math.max(...data.lats) + dlat;
		return [
			[west, south],
			[east, north]
		];
	}

	// ---- Layer building ----------------------------------------------------------

	// Build a layer colored by raw values (used for climatology).
	function buildRawGeojson(data: JobGridResponse, stops: string[]): GridFeatureCollection {
		const { lats, lons, values, min, max } = data;
		const range = max - min || 1;
		const biasMaxAbs = data.metric === 'bias' ? Math.max(Math.abs(min), Math.abs(max)) || 1 : null;
		const features: GridFeature[] = [];
		const { dlat, dlon } = gridCellBounds(data);

		for (let i = 0; i < lats.length; i++) {
			for (let j = 0; j < lons.length; j++) {
				const val = values[i]?.[j];
				if (val == null) continue;
				const lat = lats[i],
					lon = lons[j];
				const t = biasMaxAbs == null ? (val - min) / range : (val + biasMaxAbs) / (2 * biasMaxAbs);
				const color = interpolateStops(stops, t);
				const coords = [
					[lon - dlon, lat - dlat],
					[lon + dlon, lat - dlat],
					[lon + dlon, lat + dlat],
					[lon - dlon, lat + dlat],
					[lon - dlon, lat - dlat]
				];
				features.push({
					type: 'Feature',
					properties: {
						color,
						displayVal: `${data.metric}: ${val.toFixed(3)}`,
						lat,
						lon
					},
					geometry: { type: 'Polygon', coordinates: [coords] }
				});
			}
		}
		return { type: 'FeatureCollection', features };
	}

	// Build a layer colored by (model − reference) delta using a diverging scale.
	// Returns the layer and the symmetric maxAbs used for the scale.
	function buildDeltaGeojson(
		data: JobGridResponse,
		referenceData: JobGridResponse
	): { geojson: GridFeatureCollection; maxAbs: number } {
		const { lats, lons, values } = data;
		const features: GridFeature[] = [];
		const { dlat, dlon } = gridCellBounds(data);

		// First pass: collect deltas to find symmetric range
		const deltas: (number | null)[][] = lats.map((_, i) =>
			lons.map((__, j) => {
				const modelVal = values[i]?.[j];
				const referenceVal = referenceData.values[i]?.[j];
				if (modelVal == null || referenceVal == null) return null;
				return modelVal - referenceVal;
			})
		);
		let maxAbs = 0;
		for (const row of deltas) {
			for (const d of row) {
				if (d != null) maxAbs = Math.max(maxAbs, Math.abs(d));
			}
		}
		if (maxAbs === 0) maxAbs = 1;

		for (let i = 0; i < lats.length; i++) {
			for (let j = 0; j < lons.length; j++) {
				const delta = deltas[i]?.[j];
				if (delta == null) continue;
				const modelVal = values[i]?.[j] as number;
				const lat = lats[i],
					lon = lons[j];
				// Map [-maxAbs, maxAbs] to the scale. For skill metrics like ACC,
				// positive deltas are better, so invert the color direction.
				const t = isHigherBetterMetric(data.metric)
					? (maxAbs - delta) / (2 * maxAbs)
					: (delta + maxAbs) / (2 * maxAbs);
				const color = interpolateStops(DIVERGING_STOPS, t);
				const coords = [
					[lon - dlon, lat - dlat],
					[lon + dlon, lat - dlat],
					[lon + dlon, lat + dlat],
					[lon - dlon, lat + dlat],
					[lon - dlon, lat - dlat]
				];
				features.push({
					type: 'Feature',
					properties: {
						color,
						displayVal: `${data.metric}: ${modelVal.toFixed(3)} (Delta: ${delta >= 0 ? '+' : ''}${delta.toFixed(3)})`,
						lat,
						lon
					},
					geometry: { type: 'Polygon', coordinates: [coords] }
				});
			}
		}
		return {
			geojson: { type: 'FeatureCollection', features },
			maxAbs
		};
	}

	// ---- Layer management --------------------------------------------------------

	function fitToLayer(state: LayerState) {
		if (map && state.bounds) map.fitBounds(state.bounds, { padding: 28, duration: 300 });
	}

	function addLayerState(key: string, state: LayerState) {
		if (!map) return;
		removeMapLayer(state.layerId, state.sourceId);
		map.addSource(state.sourceId, {
			type: 'geojson',
			data: state.geojson as GeoJSON.GeoJSON
		});
		map.addLayer({
			id: state.layerId,
			type: 'fill',
			source: state.sourceId,
			layout: {
				visibility: visibleKeys.has(key) ? 'visible' : 'none'
			},
			paint: {
				'fill-color': ['get', 'color'],
				'fill-opacity': opacities[key] ?? 1,
				'fill-outline-color': 'rgba(255,255,255,0.3)'
			}
		});
		layers = { ...layers, [key]: state };
		if (visibleKeys.has(key)) {
			if (visibleKeys.size === 1) fitToLayer(state);
		}
	}

	function removeMapLayer(layerId: string, sourceId: string) {
		if (!map) return;
		if (map.getLayer(layerId)) map.removeLayer(layerId);
		if (map.getSource(sourceId)) map.removeSource(sourceId);
	}

	function boundaryCacheKey(level: BoundaryLevel, region: string) {
		return `${region.trim().toLowerCase()}||${level}`;
	}

	function boundaryCacheEntry(
		metadata: BoundaryMetadata,
		geojson: unknown,
		level: BoundaryLevel
	): BoundaryCacheEntry {
		return {
			label: `${metadata.boundaryName ?? 'Region'} ${metadata.boundaryType ?? BOUNDARY_LEVELS[level].type}`,
			source: metadata.boundarySource ?? 'geoBoundaries',
			geojson
		};
	}

	function addBoundaryLayerState(
		level: BoundaryLevel,
		entry: BoundaryCacheEntry
	): BoundaryLayerState | null {
		if (!map) return null;
		const style = BOUNDARY_LEVELS[level];
		const sourceId = boundarySourceId(level);
		const haloLayerId = `${boundaryLayerId(level)}-halo`;
		const layerId = boundaryLayerId(level);
		if (map.getLayer(haloLayerId)) map.removeLayer(haloLayerId);
		removeMapLayer(layerId, sourceId);
		map.addSource(sourceId, {
			type: 'geojson',
			data: entry.geojson as GeoJSON.GeoJSON
		});
		const visibility = visibleBoundaryLevels.has(level) ? 'visible' : 'none';
		map.addLayer({
			id: haloLayerId,
			type: 'line',
			source: sourceId,
			layout: { visibility },
			paint: {
				'line-color': style.haloColor,
				'line-width': style.haloWidth
			}
		});
		map.addLayer({
			id: layerId,
			type: 'line',
			source: sourceId,
			layout: { visibility },
			paint: {
				'line-color': style.strokeColor,
				'line-width': style.strokeWidth
			}
		});
		return {
			layerId,
			sourceId,
			label: entry.label,
			source: entry.source,
			geojson: entry.geojson
		};
	}

	async function loadBoundaryLayer(level: BoundaryLevel) {
		if (!map || boundaryLayers[level] || boundaryLoading.has(level)) return;
		const region = boundaryRegion;
		if (!region) {
			boundaryErrors = { ...boundaryErrors, [level]: 'No geoBoundaries mapping for this region' };
			return;
		}
		const cacheKey = boundaryCacheKey(level, region);
		const cached = boundaryCache.get(cacheKey);
		if (cached) {
			const state = addBoundaryLayerState(level, cached);
			if (state) boundaryLayers = { ...boundaryLayers, [level]: state };
			return;
		}

		boundaryLoading = new Set([...boundaryLoading, level]);
		boundaryErrors = { ...boundaryErrors, [level]: '' };
		try {
			const { metadata, geojson } = await getRegionBoundary(region, level);
			const cachedState = boundaryCacheEntry(metadata, geojson, level);
			boundaryCache.set(cacheKey, cachedState);
			const state = addBoundaryLayerState(level, cachedState);
			if (state) boundaryLayers = { ...boundaryLayers, [level]: state };
		} catch (e) {
			boundaryErrors = {
				...boundaryErrors,
				[level]: e instanceof Error ? e.message : 'Failed to load boundaries'
			};
			const next = new Set(visibleBoundaryLevels);
			next.delete(level);
			visibleBoundaryLevels = next;
		} finally {
			const next = new Set(boundaryLoading);
			next.delete(level);
			boundaryLoading = next;
		}
	}

	function toggleBoundaryLayer(level: BoundaryLevel) {
		const next = new Set(visibleBoundaryLevels);
		if (next.has(level)) {
			next.delete(level);
			setBoundaryVisibility(level, false);
		} else {
			next.add(level);
			setBoundaryVisibility(level, true);
			untrack(() => loadBoundaryLayer(level));
		}
		visibleBoundaryLevels = next;
	}

	function setBoundaryVisibility(level: BoundaryLevel, visible: boolean) {
		if (!map) return;
		for (const id of [`${boundaryLayerId(level)}-halo`, boundaryLayerId(level)]) {
			if (map.getLayer(id)) {
				map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
			}
		}
	}

	// ---- Full load (called when jobs or window changes) --------------------------

	async function loadAll() {
		if (!map || !mapReady) return;
		const requestId = ++loadRequestId;
		const previousVisibleKeys = new Set(visibleKeys);
		const previousOpacities = { ...opacities };

		for (const { layerId, sourceId } of Object.values(layers)) removeMapLayer(layerId, sourceId);
		for (const state of Object.values(boundaryLayers)) {
			if (!state) continue;
			if (map.getLayer(`${state.layerId}-halo`)) map.removeLayer(`${state.layerId}-halo`);
			removeMapLayer(state.layerId, state.sourceId);
		}
		layers = {};
		boundaryLayers = { adm1: null, adm2: null };
		errors = {};
		loading = new Set();
		boundaryErrors = {};
		boundaryLoading = new Set();

		if (jobs.length === 0 || metrics.length === 0) {
			activeRuns = [];
			visibleKeys = new Set();
			opacities = {};
			return;
		}

		// One RunDef per forecast model
		const modelRuns: RunDef[] = jobs.map((job, i) => ({
			jobId: job.id,
			modelName: job.model_name,
			colorIndex: i
		}));

		// One climatology RunDef using the first job's output (clim is identical across jobs in a group)
		const climRun: RunDef = {
			jobId: jobs[0].id,
			modelName: 'climatology',
			colorIndex: jobs.length // distinct color index after all model colors
		};

		const firstMetric = metrics[0].value;
		if (!selectedMetric || !metrics.some((metric) => metric.value === selectedMetric)) {
			selectedMetric = firstMetric;
		}
		if (!selectedModelJobId || !modelRuns.some((run) => run.jobId === selectedModelJobId)) {
			selectedModelJobId = modelRuns[0].jobId;
		}
		if (
			selectedReferenceJobId !== 'climatology' &&
			!modelRuns.some((run) => run.jobId === selectedReferenceJobId)
		) {
			selectedReferenceJobId = 'climatology';
		}
		if (selectedReferenceJobId === selectedModelJobId) {
			selectedReferenceJobId = 'climatology';
		}

		// Mark all keys as loading
		const fetchRuns = [...modelRuns, climRun];
		const allKeys = fetchRuns.flatMap((run) =>
			metrics.map((m) => rawLayerKey(run.jobId, run.modelName, m.value))
		);
		visibleKeys = new Set([...previousVisibleKeys].filter((key) => allKeys.includes(key)));
		opacities = Object.fromEntries(allKeys.map((key) => [key, previousOpacities[key] ?? 1]));
		loading = new Set(allKeys);

		// Fetch all grid data concurrently
		type FetchResult =
			| { run: RunDef; metricValue: string; data: JobGridResponse }
			| { run: RunDef; metricValue: string; error: string };
		const results: FetchResult[] = await Promise.all(
			fetchRuns.flatMap((run) =>
				metrics.map(async (m) => {
					try {
						const data = await getCachedJobGrid(run.jobId, run.modelName, forecastWindow, m.value);
						return { run, metricValue: m.value, data };
					} catch (e) {
						return {
							run,
							metricValue: m.value,
							error: e instanceof Error ? e.message : 'Failed to load'
						};
					}
				})
			)
		);
		if (requestId !== loadRequestId) return;

		// Index climatology data by metric for delta computation
		const climByMetric: Record<string, JobGridResponse> = {};
		const dataByRunMetric: Record<string, JobGridResponse> = {};
		for (const r of results) {
			if ('data' in r) {
				dataByRunMetric[rawLayerKey(r.run.jobId, r.run.modelName, r.metricValue)] = r.data;
				if (r.run.modelName === 'climatology') {
					climByMetric[r.metricValue] = r.data;
				}
			}
		}
		const hasClimatology = Object.keys(climByMetric).length > 0;
		activeRuns = hasClimatology ? [...modelRuns, climRun] : modelRuns;

		// Build raw value layers.
		const newErrors: Record<string, string> = {};
		for (const r of results) {
			const key = rawLayerKey(r.run.jobId, r.run.modelName, r.metricValue);
			if ('error' in r) {
				if (r.run.modelName !== 'climatology') newErrors[key] = r.error;
			} else {
				const { run, metricValue, data } = r;
				const layerId = mapLayerId(key);
				const sourceId = mapSourceId(key);
				const bounds = boundsFromGrid(data);
				const stops = getStops(metricValue, run.colorIndex);
				const geojson = buildRawGeojson(data, stops);
				addLayerState(key, { layerId, sourceId, data, geojson, bounds, stops, isDelta: false });
			}
		}

		// Build model-vs-baseline and model-vs-model difference layers.
		for (const run of modelRuns) {
			for (const metric of metrics) {
				const modelData = dataByRunMetric[rawLayerKey(run.jobId, run.modelName, metric.value)];
				if (!modelData) continue;
				const references = activeRuns.filter((reference) => !sameRun(reference, run));
				for (const reference of references) {
					const referenceData =
						dataByRunMetric[rawLayerKey(reference.jobId, reference.modelName, metric.value)];
					if (!referenceData) continue;
					const key = deltaLayerKey(
						run.jobId,
						run.modelName,
						metric.value,
						reference.jobId,
						reference.modelName
					);
					const { geojson, maxAbs } = buildDeltaGeojson(modelData, referenceData);
					addLayerState(key, {
						layerId: mapLayerId(key),
						sourceId: mapSourceId(key),
						data: modelData,
						geojson,
						bounds: boundsFromGrid(modelData),
						stops: DIVERGING_STOPS,
						isDelta: true,
						deltaMaxAbs: maxAbs,
						referenceData,
						referenceModelName: reference.modelName
					});
					opacities = { ...opacities, [key]: previousOpacities[key] ?? 1 };
				}
			}
		}
		errors = newErrors;
		loading = new Set();

		applyLensSelection(true);
		const firstVisibleKey = [...visibleKeys][0];
		if (firstVisibleKey) fitToLayer(layers[firstVisibleKey]);

		if (Object.values(layers).length > 0) {
			for (const level of visibleBoundaryLevels) untrack(() => loadBoundaryLayer(level));
		}
	}

	// ---- Map setup ---------------------------------------------------------------

	onMount(() => {
		if (!mapContainer) return;
		map = new maplibregl.Map({
			container: mapContainer,
			style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
			center: [80, 20],
			zoom: 4,
			attributionControl: false
		});
		map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
		map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
		map.on('load', () => {
			mapReady = true;
			const attrib = mapContainer?.querySelector<HTMLDetailsElement>('.maplibregl-ctrl-attrib');
			if (attrib) {
				attrib.classList.remove('maplibregl-compact-show');
				attrib.removeAttribute('open');
			}
		});
		map.on('move', () => {
			if (viewMode === 'swipe') updateSwipeFilters();
		});

		const onPointerMove = (event: PointerEvent) => {
			if (!draggingSwipe) return;
			setSwipePositionFromClientX(event.clientX);
		};
		const onPointerUp = () => {
			draggingSwipe = false;
		};
		window.addEventListener('pointermove', onPointerMove);
		window.addEventListener('pointerup', onPointerUp);

		map.on('mousemove', (e) => {
			if (!map) {
				tooltipVisible = false;
				return;
			}
			const layerIds = [...visibleKeys]
				.map((key) => layers[key]?.layerId)
				.filter((id): id is string => Boolean(id && map?.getLayer(id)));
			const feature =
				layerIds.length > 0 ? map.queryRenderedFeatures(e.point, { layers: layerIds })[0] : null;
			const lat = feature?.properties?.lat;
			const lon = feature?.properties?.lon;
			if (typeof lat === 'number' && typeof lon === 'number') {
				tooltipContent = buildTooltipContent(lat, lon);
				tooltipX = e.point.x + 14;
				tooltipY = e.point.y - 10;
				tooltipVisible = true;
				mapContainer!.style.cursor = 'crosshair';
			} else {
				tooltipVisible = false;
				mapContainer!.style.cursor = '';
			}
		});

		map.on('click', (e) => {
			if (!map) return;
			const layerIds = [...visibleKeys]
				.map((key) => layers[key]?.layerId)
				.filter((id): id is string => Boolean(id && map?.getLayer(id)));
			const feature =
				layerIds.length > 0 ? map.queryRenderedFeatures(e.point, { layers: layerIds })[0] : null;
			const lat = feature?.properties?.lat;
			const lon = feature?.properties?.lon;
			if (typeof lat !== 'number' || typeof lon !== 'number') return;
			openCellInspector(lat, lon);
		});

		return () => {
			window.removeEventListener('pointermove', onPointerMove);
			window.removeEventListener('pointerup', onPointerUp);
		};
	});

	onDestroy(() => {
		if (map) {
			map.remove();
			map = null;
		}
		mapReady = false;
	});

	$effect(() => {
		// Only trigger on job set or window changes — do NOT read jobs/metrics directly
		// as that would re-run loadAll on every poll even when complete jobs are unchanged.
		if (jobIds && forecastWindow && map && mapReady) untrack(loadAll);
	});

	$effect(() => {
		selectedMetric;
		selectedModelJobId;
		selectedReferenceJobId;
		viewMode;
		swipePosition;
		if (map && mapReady && Object.keys(layers).length > 0) {
			untrack(() => applyLensSelection(false));
		}
	});

	$effect(() => {
		const cell = selectedCell;
		if (cell && jobIds && forecastWindow) {
			const jobsSnapshot = jobs;
			const window = forecastWindow;
			untrack(() => loadCellResults(cell.lat, cell.lon, window, jobsSnapshot));
		}
	});

	$effect(() => {
		fullscreen;
		setTimeout(() => map?.resize(), 300);
	});
</script>

<div class="map-root" class:fullscreen>
	{#if anyLoading}
		<div class="status-overlay">Loading…</div>
	{/if}

	<button
		class="fullscreen-btn"
		onclick={() => (fullscreen = !fullscreen)}
		title={fullscreen ? 'Exit fullscreen' : 'Expand map'}
	>
		{#if fullscreen}⤡{:else}⤢{/if}
	</button>

	<div bind:this={mapContainer} class="map-instance"></div>

	<!-- Result lens -->
	<div class="layer-panel result-lens" class:collapsed={panelCollapsed}>
		<button class="layer-panel-header" onclick={() => (panelCollapsed = !panelCollapsed)}>
			<span class="layer-panel-title">Result lens</span>
			<span class="panel-toggle">{panelCollapsed ? '▸' : '▾'}</span>
		</button>

		{#if !panelCollapsed}
			<div class="lens-controls">
				<label class="control-field">
					<span>Metric</span>
					<select bind:value={selectedMetric}>
						{#each metrics as metric}
							<option value={metric.value}>{metric.label}</option>
						{/each}
					</select>
				</label>

				<div class="view-toggle" aria-label="Map view">
					<button class:active={viewMode === 'single'} onclick={() => (viewMode = 'single')}>
						Values
					</button>
					<button
						class:active={viewMode === 'baseline'}
						onclick={() => {
							viewMode = 'baseline';
							selectedReferenceJobId = 'climatology';
						}}
					>
						Skill
					</button>
					<button class:active={viewMode === 'difference'} onclick={() => (viewMode = 'difference')}>
						Difference
					</button>
					<button class:active={viewMode === 'swipe'} onclick={() => (viewMode = 'swipe')}>
						Swipe
					</button>
				</div>
				<p class="lens-note">{viewModeDescription(viewMode)}</p>

				<label class="control-field">
					<span>Model</span>
					<select bind:value={selectedModelJobId}>
						{#each availableModelRuns() as run}
							<option value={run.jobId}>{modelRunLabel(run)}</option>
						{/each}
					</select>
				</label>

				{#if viewMode === 'difference' || viewMode === 'swipe'}
					<label class="control-field">
						<span>Compare with</span>
						<select bind:value={selectedReferenceJobId}>
							{#if activeRuns.some((run) => run.modelName === 'climatology')}
								<option value="climatology">Climatology</option>
							{/if}
							{#each availableModelRuns().filter((run) => run.jobId !== selectedModelJobId) as run}
								<option value={run.jobId}>{modelRunLabel(run)}</option>
							{/each}
						</select>
					</label>
				{:else if viewMode === 'baseline'}
					<p class="lens-note">Blue is better for error metrics; red is worse.</p>
				{/if}
			</div>

			<div class="boundary-group">
				<div class="run-header">
					<span class="run-label">Boundaries</span>
				</div>

				{#each Object.entries(BOUNDARY_LEVELS) as [level, def]}
					{@const boundaryLevel = level as BoundaryLevel}
					{@const isVisible = visibleBoundaryLevels.has(boundaryLevel)}
					{@const isLoading = boundaryLoading.has(boundaryLevel)}
					{@const err = boundaryErrors[boundaryLevel]}

					<div class="layer-item" class:visible={isVisible}>
						<button class="layer-row" onclick={() => toggleBoundaryLayer(boundaryLevel)}>
							<span class="layer-checkbox" class:checked={isVisible}>
								{#if isVisible}<span class="checkmark">✓</span>{/if}
							</span>
							<span class="layer-label">{def.label}</span>
							<span class="osm-level-chip">{def.type}</span>
							{#if isLoading}
								<span class="layer-spinner"></span>
							{:else if err}
								<span class="layer-error" title={err}>✕</span>
							{/if}
						</button>
						{#if err}
							<p class="boundary-error">{err}</p>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>

	{#if tooltipVisible}
		<div class="tooltip" style="left: {tooltipX}px; top: {tooltipY}px">
			{@html tooltipContent}
		</div>
	{/if}

	{#if viewMode === 'swipe' && visibleLayers.length === 2}
		<div class="swipe-split" style="left: {swipePosition}%">
			<div class="swipe-line"></div>
			<div
				class="swipe-handle"
				class:dragging={draggingSwipe}
				onpointerdown={startSwipeDrag}
				onkeydown={moveSwipeWithKeyboard}
				role="slider"
				tabindex="0"
				aria-label="Swipe comparison position"
				aria-valuemin="5"
				aria-valuemax="95"
				aria-valuenow={Math.round(swipePosition)}
			>
				<span>‹</span>
				<span>›</span>
			</div>
		</div>
	{/if}

	<!-- Legend: one entry per visible layer -->
	{#if visibleLayers.length > 0}
		<div class="legend">
			{#each visibleLayers as vl, i}
				{@const { modelName: vModel, metric: vMetric } = parseKey(vl.key)}
				{@const gradient = `linear-gradient(to right, ${vl.stops.join(', ')})`}
				{@const displayName = modelDisplayName(vModel)}
				{@const referenceName = vl.referenceModelName ? modelDisplayName(vl.referenceModelName) : null}
				{#if i > 0}<div class="legend-divider"></div>{/if}
				<div class="legend-title">
					{displayName} — {metricLabel(vMetric)}
					{#if vl.isDelta && referenceName}<span class="legend-delta-badge">Δ vs {referenceName}</span>{/if}
				</div>
				<div class="scale-bar" style="background: {gradient}"></div>
				{#if vl.isDelta && vl.deltaMaxAbs != null}
					<div class="scale-labels">
						<span>−{vl.deltaMaxAbs.toFixed(3)}</span>
						<span class="scale-unit"
							>{isNeutralDeltaMetric(vMetric)
								? '(negative)'
								: isHigherBetterMetric(vMetric)
									? '(worse)'
									: '(better)'}</span
						>
						<span>0</span>
						<span class="scale-unit"
							>{isNeutralDeltaMetric(vMetric)
								? '(positive)'
								: isHigherBetterMetric(vMetric)
									? '(better)'
									: '(worse)'}</span
						>
						<span>+{vl.deltaMaxAbs.toFixed(3)}</span>
					</div>
				{:else}
					<div class="scale-labels">
						<span>{vl.data.min.toFixed(2)}</span>
						<span class="scale-unit">({vl.data.unit})</span>
						<span>{vl.data.max.toFixed(2)}</span>
					</div>
				{/if}
			{/each}
		</div>
	{/if}

	{#if visibleBoundaryLayers.length > 0}
		<div class="boundary-attribution">
			Boundaries: geoBoundaries gbOpen
			{#each visibleBoundaryLayers as boundaryLayer, i}
				{#if i === 0}({:else};
				{/if}{boundaryLayer.label}{#if i === visibleBoundaryLayers.length - 1}){/if}
			{/each}
		</div>
	{/if}

	{#if selectedCell}
		<GridCellInspector
			cell={selectedCell}
			{forecastWindow}
			{metrics}
			results={cellResults}
			loading={cellLoading}
			error={cellError}
			onclose={closeCellInspector}
		/>
	{/if}
</div>

<style>
	/* ---- Map root ---- */
	.map-root {
		position: relative;
		width: 100%;
		height: 600px;
		border: 1px solid var(--color-border-subtle);
		border-radius: 0.5rem;
		overflow: hidden;
		background: var(--color-surface);
		transition:
			height 0.3s ease,
			border-radius 0.3s ease;
	}

	.map-root.fullscreen {
		position: fixed;
		inset: 0;
		height: 100dvh;
		width: 100dvw;
		border-radius: 0;
		z-index: 900;
		border: none;
	}

	:global(.map-root.fullscreen.obscured-by-lightbox) {
		z-index: 1;
	}

	.map-instance {
		width: 100%;
		height: 100%;
	}
	.map-instance :global(.maplibregl-canvas-container),
	.map-instance :global(.maplibregl-canvas) {
		width: 100% !important;
		height: 100% !important;
	}
	.map-instance :global(.maplibregl-canvas) {
		border-radius: 0.5rem;
	}
	.map-instance :global(.maplibregl-ctrl button) {
		background-color: rgba(255, 255, 255, 0.85);
		color: #333;
		border-radius: 0.25rem;
	}
	.map-instance :global(.maplibregl-ctrl-bottom-right) {
		bottom: 0.5rem;
		right: 0.5rem;
	}

	/* ---- Status overlay ---- */
	.status-overlay {
		position: absolute;
		top: 1rem;
		left: 50%;
		transform: translateX(-50%);
		z-index: 20;
		padding: 0.4rem 1rem;
		border-radius: 2rem;
		font-size: 0.78rem;
		font-weight: 600;
		pointer-events: none;
		white-space: nowrap;
		background: var(--color-accent);
		color: var(--color-bg);
	}

	/* ---- Fullscreen button ---- */
	.fullscreen-btn {
		position: absolute;
		bottom: 1.5rem;
		left: 0.75rem;
		z-index: 20;
		width: 2rem;
		height: 2rem;
		background: rgba(255, 255, 255, 0.9);
		border: 1px solid #ccc;
		border-radius: 0.3rem;
		box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
		cursor: pointer;
		font-size: 0.9rem;
		display: flex;
		align-items: center;
		justify-content: center;
		color: #444;
		transition: background-color 0.1s;
	}
	.fullscreen-btn:hover {
		background: white;
		color: #111;
	}

	/* ---- Layer panel ---- */
	.layer-panel {
		position: absolute;
		top: 0.75rem;
		right: 0.75rem;
		z-index: 20;
		background: rgba(255, 255, 255, 0.96);
		border: 1px solid #ccc;
		border-radius: 0.4rem;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
		width: clamp(14rem, 24vw, 20rem);
		overflow: hidden;
		max-height: calc(100% - 1.5rem);
		overflow-y: auto;
	}

	.lens-controls {
		display: flex;
		flex-direction: column;
		gap: 0.65rem;
		padding: 0.65rem;
		border-top: 1px solid #e1e5ea;
		background: rgba(250, 248, 242, 0.96);
	}

	.control-field {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.control-field span {
		font-size: 0.58rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.09em;
		color: #6f6b62;
	}

	.control-field select {
		width: 100%;
		min-width: 0;
		border: 1px solid #d7d0c2;
		border-radius: 0.35rem;
		background: rgba(255, 255, 255, 0.9);
		color: #2d2a25;
		font: inherit;
		font-size: 0.78rem;
		padding: 0.45rem 0.5rem;
	}

	.view-toggle {
		display: flex;
		padding: 0.18rem;
		border: 1px solid #d7d0c2;
		border-radius: 0.45rem;
		background: #eee8dd;
	}

	.view-toggle button {
		flex: 1;
		border: none;
		border-radius: 0.32rem;
		background: transparent;
		color: #6f6b62;
		font: inherit;
		font-size: 0.68rem;
		font-weight: 700;
		padding: 0.35rem 0.3rem;
		cursor: pointer;
	}

	.view-toggle button + button {
		margin-left: 0.05rem;
	}

	.view-toggle button.active {
		background: white;
		color: var(--color-accent);
		box-shadow: 0 1px 3px rgba(31, 26, 18, 0.12);
	}

	.lens-note {
		margin: 0;
		font-size: 0.68rem;
		line-height: 1.35;
		color: #6f6b62;
	}

	.layer-panel-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		width: 100%;
		padding: 0.45rem 0.5rem 0.35rem;
		border: none;
		background: rgba(255, 255, 255, 0.96);
		cursor: pointer;
		font-family: var(--font-body);
		position: sticky;
		top: 0;
		z-index: 1;
	}
	.layer-panel-header:hover {
		background: rgba(0, 0, 0, 0.03);
	}

	.layer-panel-title {
		font-size: 0.58rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: #888;
		margin: 0;
	}
	.panel-toggle {
		font-size: 0.6rem;
		color: #aaa;
	}

	.boundary-group {
		border-top: 1px solid #e1e5ea;
		background: rgba(246, 248, 250, 0.88);
	}

	.run-header {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.45rem 0.5rem 0.25rem;
	}

	.run-label {
		font-size: 0.72rem;
		font-weight: 700;
		color: #222;
	}

	/* ---- Layer rows ---- */
	.layer-item {
		display: flex;
		flex-direction: column;
	}

	.layer-row {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		width: 100%;
		padding: 0.28rem 0.5rem;
		border: none;
		background: none;
		cursor: pointer;
		font-family: var(--font-body);
		transition: background-color 0.1s;
		box-sizing: border-box;
	}
	.layer-row:hover:not(:disabled) {
		background: rgba(0, 0, 0, 0.04);
	}
	.layer-row:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.layer-checkbox {
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 0.2rem;
		border: 1.5px solid #bbb;
		background: white;
		flex-shrink: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		transition:
			background-color 0.1s,
			border-color 0.1s;
	}
	.layer-checkbox.checked {
		background: var(--color-accent, #3b82f6);
		border-color: var(--color-accent, #3b82f6);
	}
	.checkmark {
		font-size: 0.55rem;
		color: white;
		line-height: 1;
		font-weight: 700;
	}

	.layer-label {
		font-size: 0.75rem;
		font-weight: 500;
		color: #333;
		flex: 1;
		text-align: left;
	}
	.layer-item.visible .layer-label {
		font-weight: 600;
		color: #111;
	}

	.layer-spinner {
		width: 0.65rem;
		height: 0.65rem;
		border: 1.5px solid #ddd;
		border-top-color: #888;
		border-radius: 50%;
		animation: spin 0.7s linear infinite;
		flex-shrink: 0;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.layer-error {
		font-size: 0.7rem;
		color: #c00;
	}

	.osm-level-chip {
		font-size: 0.52rem;
		font-weight: 700;
		line-height: 1;
		color: #596273;
		background: #eef1f4;
		border: 1px solid #d8dde5;
		padding: 0.16rem 0.28rem;
		border-radius: 0.22rem;
		flex-shrink: 0;
	}

	.boundary-error {
		margin: 0;
		padding: 0 0.5rem 0.35rem 1.85rem;
		color: #9b1c1c;
		font-size: 0.62rem;
		line-height: 1.25;
	}

	/* ---- Tooltip ---- */
	.tooltip {
		position: absolute;
		z-index: 30;
		background: white;
		border: 1px solid #ccc;
		border-radius: 0.3rem;
		padding: 0.4rem 0.6rem;
		font-size: 0.75rem;
		font-family: var(--font-body);
		color: #333;
		box-shadow: 0 2px 6px rgba(0, 0, 0, 0.12);
		pointer-events: none;
		line-height: 1.5;
		min-width: 160px;
	}

	.tooltip :global(.tt-group) {
		margin-top: 0.35rem;
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
	}

	.tooltip :global(.tt-model) {
		display: block;
		font-size: 0.65rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: #777;
		margin-bottom: 0.1rem;
	}

	.tooltip :global(.tt-metric) {
		display: block;
		font-size: 0.73rem;
		color: #222;
		font-family: var(--font-mono);
	}

	.tooltip :global(.tt-delta) {
		font-size: 0.68rem;
		color: #777;
	}

	.swipe-split {
		position: absolute;
		top: 0;
		bottom: 0;
		z-index: 24;
		pointer-events: none;
	}

	.swipe-line {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 2px;
		background: rgba(255, 255, 255, 0.92);
		box-shadow: 0 0 0 1px rgba(30, 37, 44, 0.35);
		transform: translateX(-1px);
	}

	.swipe-handle {
		position: absolute;
		top: 50%;
		left: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.1rem;
		width: 2.35rem;
		height: 2.35rem;
		border-radius: 999px;
		border: 1px solid rgba(35, 41, 46, 0.28);
		background: rgba(255, 255, 255, 0.94);
		color: #2f4f47;
		font-size: 1rem;
		font-weight: 800;
		box-shadow: 0 0.35rem 1.2rem rgba(0, 0, 0, 0.22);
		transform: translate(-50%, -50%);
		cursor: ew-resize;
		pointer-events: auto;
		user-select: none;
	}

	.swipe-handle.dragging,
	.swipe-handle:focus-visible {
		outline: none;
		border-color: var(--color-accent);
		box-shadow:
			0 0 0 3px rgba(67, 122, 111, 0.25),
			0 0.35rem 1.2rem rgba(0, 0, 0, 0.22);
	}

	/* ---- Legend ---- */
	.legend {
		position: absolute;
		bottom: 1rem;
		left: 50%;
		transform: translateX(-50%);
		z-index: 20;
		background: rgba(255, 255, 255, 0.92);
		padding: 0.6rem 0.75rem;
		border-radius: 0.4rem;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
		border: 1px solid #ccc;
		width: min(24rem, calc(100% - 7rem));
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
	}

	:global(body.figure-lightbox-open) .map-root .layer-panel,
	:global(body.figure-lightbox-open) .map-root .legend,
	:global(body.figure-lightbox-open) .map-root .boundary-attribution,
	:global(body.figure-lightbox-open) .map-root .fullscreen-btn,
	:global(body.figure-lightbox-open) .map-root .status-overlay,
	:global(body.figure-lightbox-open) .map-root .tooltip {
		display: none;
	}

	:global(.map-root.fullscreen.obscured-by-lightbox .layer-panel),
	:global(.map-root.fullscreen.obscured-by-lightbox .legend),
	:global(.map-root.fullscreen.obscured-by-lightbox .boundary-attribution),
	:global(.map-root.fullscreen.obscured-by-lightbox .fullscreen-btn),
	:global(.map-root.fullscreen.obscured-by-lightbox .status-overlay),
	:global(.map-root.fullscreen.obscured-by-lightbox .tooltip) {
		display: none;
	}
	.legend-title {
		font-size: 0.6rem;
		font-weight: 700;
		color: #333;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		margin-bottom: 0.3rem;
		text-align: center;
	}
	.legend-divider {
		height: 1px;
		background: #e5e5e5;
		margin: 0.4rem 0;
	}

	.legend-delta-badge {
		display: inline-block;
		font-size: 0.5rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: #555;
		background: #eee;
		border: 1px solid #ccc;
		padding: 0.05rem 0.3rem;
		border-radius: 0.2rem;
		margin-left: 0.3rem;
		vertical-align: middle;
	}
	.scale-bar {
		height: 10px;
		border-radius: 2px;
		margin-bottom: 0.2rem;
	}
	.scale-labels {
		display: flex;
		justify-content: space-between;
		font-size: 0.6rem;
		font-family: var(--font-mono);
		color: #555;
	}
	.scale-unit {
		color: #999;
		font-size: 0.58rem;
	}

	.boundary-attribution {
		position: absolute;
		left: 3rem;
		bottom: 0.35rem;
		z-index: 20;
		max-width: calc(100% - 6rem);
		padding: 0.18rem 0.4rem;
		border-radius: 0.25rem;
		background: rgba(255, 255, 255, 0.86);
		color: #555;
		font-size: 0.58rem;
		line-height: 1.25;
		box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
	}
</style>
