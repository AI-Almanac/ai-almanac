<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import Map from 'ol/Map';
	import View from 'ol/View';
	import TileLayer from 'ol/layer/Tile';
	import VectorLayer from 'ol/layer/Vector';
	import VectorSource from 'ol/source/Vector';
	import OSM from 'ol/source/OSM';
	import Feature from 'ol/Feature';
	import Polygon from 'ol/geom/Polygon';
	import { fromLonLat } from 'ol/proj';
	import GeoJSON from 'ol/format/GeoJSON';
	import Style from 'ol/style/Style';
	import Fill from 'ol/style/Fill';
	import Stroke from 'ol/style/Stroke';
	import 'ol/ol.css';
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

	let mapContainer = $state<HTMLElement | null>(null);
	let map = $state<Map | null>(null);

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
		]
	};
	const FALLBACK_SCALE = ['#f7f7f7', '#cccccc', '#969696', '#525252', '#252525'];

	// Diverging scale for anomaly (model − climatology): blue → white → red
	// Blue = model better than baseline, red = model worse
	const DIVERGING_STOPS = ['#2166ac', '#92c5de', '#f7f7f7', '#f4a582', '#b2182b'];

	function getStops(metricValue: string, colorIndex: number): string[] {
		const scales = COLOR_SCALES[metricValue];
		if (!scales) return FALLBACK_SCALE;
		return scales[colorIndex % scales.length];
	}

	type LayerState = {
		layer: VectorLayer;
		data: JobGridResponse;
		stops: string[];
		isDelta: boolean;
		deltaMaxAbs?: number;
		climData?: JobGridResponse; // present on delta layers for tooltip computation
	};

	type BoundaryLevel = 'adm1' | 'adm2';
	type BoundaryLayerState = {
		layer: VectorLayer;
		label: string;
		source: string;
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
	const boundaryCache = new globalThis.Map<string, BoundaryLayerState>();

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
	let loadRequestId = 0;
	const boundaryRegion = $derived(jobs[0]?.params?.region);

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

	function layerKey(jobId: string, modelName: string, metric: string) {
		return `${jobId}||${modelName}||${metric}`;
	}

	function parseKey(key: string) {
		const [jobId, modelName, metric] = key.split('||');
		return { jobId, modelName, metric };
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
				if (ls.isDelta && ls.climData) {
					const climVal = getValueAtLatLon(ls.climData, lat, lon);
					const delta = climVal != null ? val - climVal : null;
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

	// ---- Layer building ----------------------------------------------------------

	// Build a layer colored by raw values (used for climatology).
	function buildRawLayer(data: JobGridResponse, stops: string[]): VectorLayer {
		const { lats, lons, values, min, max } = data;
		const range = max - min || 1;
		const features: Feature[] = [];
		const dlat = lats.length > 1 ? Math.abs(lats[1] - lats[0]) / 2 : 0.5;
		const dlon = lons.length > 1 ? Math.abs(lons[1] - lons[0]) / 2 : 0.5;

		for (let i = 0; i < lats.length; i++) {
			for (let j = 0; j < lons.length; j++) {
				const val = values[i]?.[j];
				if (val == null) continue;
				const lat = lats[i],
					lon = lons[j];
				const t = (val - min) / range;
				const color = interpolateStops(stops, t);
				const coords = [
					fromLonLat([lon - dlon, lat - dlat]),
					fromLonLat([lon + dlon, lat - dlat]),
					fromLonLat([lon + dlon, lat + dlat]),
					fromLonLat([lon - dlon, lat + dlat]),
					fromLonLat([lon - dlon, lat - dlat])
				];
				const feature = new Feature({ geometry: new Polygon([coords]) });
				feature.set('displayVal', `${data.metric}: ${val.toFixed(3)}`);
				feature.set('lat', lat);
				feature.set('lon', lon);
				feature.setStyle(
					new Style({
						fill: new Fill({ color }),
						stroke: new Stroke({ color: 'rgba(255,255,255,0.3)', width: 0.5 })
					})
				);
				features.push(feature);
			}
		}
		return new VectorLayer({ source: new VectorSource({ features }), visible: false, zIndex: 10 });
	}

	// Build a layer colored by (model − climatology) delta using a diverging scale.
	// Returns the layer and the symmetric maxAbs used for the scale.
	function buildDeltaLayer(
		data: JobGridResponse,
		climData: JobGridResponse
	): { layer: VectorLayer; maxAbs: number } {
		const { lats, lons, values } = data;
		const features: Feature[] = [];
		const dlat = lats.length > 1 ? Math.abs(lats[1] - lats[0]) / 2 : 0.5;
		const dlon = lons.length > 1 ? Math.abs(lons[1] - lons[0]) / 2 : 0.5;

		// First pass: collect deltas to find symmetric range
		const deltas: (number | null)[][] = lats.map((_, i) =>
			lons.map((__, j) => {
				const modelVal = values[i]?.[j];
				const climVal = climData.values[i]?.[j];
				if (modelVal == null || climVal == null) return null;
				return modelVal - climVal;
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
				// Map [-maxAbs, maxAbs] → [0, 1]
				const t = (delta + maxAbs) / (2 * maxAbs);
				const color = interpolateStops(DIVERGING_STOPS, t);
				const coords = [
					fromLonLat([lon - dlon, lat - dlat]),
					fromLonLat([lon + dlon, lat - dlat]),
					fromLonLat([lon + dlon, lat + dlat]),
					fromLonLat([lon - dlon, lat + dlat]),
					fromLonLat([lon - dlon, lat - dlat])
				];
				const feature = new Feature({ geometry: new Polygon([coords]) });
				feature.set(
					'displayVal',
					`${data.metric}: ${modelVal.toFixed(3)} (Δ vs climatology: ${delta >= 0 ? '+' : ''}${delta.toFixed(3)})`
				);
				feature.set('lat', lat);
				feature.set('lon', lon);
				feature.setStyle(
					new Style({
						fill: new Fill({ color }),
						stroke: new Stroke({ color: 'rgba(255,255,255,0.3)', width: 0.5 })
					})
				);
				features.push(feature);
			}
		}
		return {
			layer: new VectorLayer({
				source: new VectorSource({ features }),
				visible: false,
				zIndex: 10
			}),
			maxAbs
		};
	}

	// ---- Layer management --------------------------------------------------------

	function fitToLayer(layer: VectorLayer) {
		const extent = (layer.getSource() as VectorSource).getExtent();
		if (map && extent) map.getView().fit(extent, { padding: [20, 20, 20, 20], duration: 300 });
	}

	function addLayerState(key: string, state: LayerState) {
		if (!map) return;
		if (layers[key]) map.removeLayer(layers[key].layer);
		map.addLayer(state.layer);
		layers = { ...layers, [key]: state };
		if (visibleKeys.has(key)) {
			state.layer.setVisible(true);
			state.layer.setOpacity(opacities[key] ?? 1);
			if (visibleKeys.size === 1) fitToLayer(state.layer);
		}
	}

	function boundaryCacheKey(level: BoundaryLevel, region: string) {
		return `${region.trim().toLowerCase()}||${level}`;
	}

	function buildBoundaryLayer(
		level: BoundaryLevel,
		metadata: BoundaryMetadata,
		geojson: unknown
	): BoundaryLayerState {
		const features = new GeoJSON().readFeatures(geojson, {
			dataProjection: 'EPSG:4326',
			featureProjection: 'EPSG:3857'
		});
		const style = BOUNDARY_LEVELS[level];
		const layer = new VectorLayer({
			source: new VectorSource({ features }),
			visible: false,
			zIndex: style.zIndex,
			style: [
				new Style({ stroke: new Stroke({ color: style.haloColor, width: style.haloWidth }) }),
				new Style({
					stroke: new Stroke({
						color: style.strokeColor,
						width: style.strokeWidth
					})
				})
			]
		});
		return {
			layer,
			label: `${metadata.boundaryName ?? 'Region'} ${metadata.boundaryType ?? BOUNDARY_LEVELS[level].type}`,
			source: metadata.boundarySource ?? 'geoBoundaries'
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
			map.addLayer(cached.layer);
			cached.layer.setVisible(visibleBoundaryLevels.has(level));
			boundaryLayers = { ...boundaryLayers, [level]: cached };
			return;
		}

		boundaryLoading = new Set([...boundaryLoading, level]);
		boundaryErrors = { ...boundaryErrors, [level]: '' };
		try {
			const { metadata, geojson } = await getRegionBoundary(region, level);
			const state = buildBoundaryLayer(level, metadata, geojson);
			boundaryCache.set(cacheKey, state);
			map.addLayer(state.layer);
			state.layer.setVisible(visibleBoundaryLevels.has(level));
			boundaryLayers = { ...boundaryLayers, [level]: state };
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
			boundaryLayers[level]?.layer.setVisible(false);
		} else {
			next.add(level);
			boundaryLayers[level]?.layer.setVisible(true);
			untrack(() => loadBoundaryLayer(level));
		}
		visibleBoundaryLevels = next;
	}

	function toggleLayer(key: string) {
		const next = new Set(visibleKeys);
		if (next.has(key)) {
			next.delete(key);
			layers[key]?.layer.setVisible(false);
		} else {
			next.add(key);
			if (layers[key]) {
				layers[key].layer.setVisible(true);
				layers[key].layer.setOpacity(opacities[key] ?? 1);
				if (visibleKeys.size === 0) fitToLayer(layers[key].layer);
			}
		}
		visibleKeys = next;
	}

	function setOpacity(key: string, value: number) {
		opacities = { ...opacities, [key]: value };
		layers[key]?.layer.setOpacity(value);
	}

	// ---- Full load (called when jobs or window changes) --------------------------

	async function loadAll() {
		if (!map) return;
		const requestId = ++loadRequestId;
		const previousVisibleKeys = new Set(visibleKeys);
		const previousOpacities = { ...opacities };

		for (const { layer } of Object.values(layers)) map.removeLayer(layer);
		for (const state of Object.values(boundaryLayers)) {
			if (state) map.removeLayer(state.layer);
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

		activeRuns = [...modelRuns, climRun];

		const firstKey = layerKey(modelRuns[0].jobId, modelRuns[0].modelName, metrics[0].value);

		// Mark all keys as loading
		const allKeys = activeRuns.flatMap((run) =>
			metrics.map((m) => layerKey(run.jobId, run.modelName, m.value))
		);
		const allKeySet = new Set(allKeys);
		const preservedVisibleKeys = [...previousVisibleKeys].filter((key) => allKeySet.has(key));
		visibleKeys = new Set(preservedVisibleKeys.length > 0 ? preservedVisibleKeys : [firstKey]);
		opacities = Object.fromEntries(allKeys.map((key) => [key, previousOpacities[key] ?? 1]));
		loading = new Set(allKeys);

		// Fetch all grid data concurrently
		type FetchResult =
			| { run: RunDef; metricValue: string; data: JobGridResponse }
			| { run: RunDef; metricValue: string; error: string };
		const results: FetchResult[] = await Promise.all(
			activeRuns.flatMap((run) =>
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
		for (const r of results) {
			if ('data' in r && r.run.modelName === 'climatology') {
				climByMetric[r.metricValue] = r.data;
			}
		}

		// Build and register layers
		const newErrors: Record<string, string> = {};
		for (const r of results) {
			const key = layerKey(r.run.jobId, r.run.modelName, r.metricValue);
			if ('error' in r) {
				newErrors[key] = r.error;
			} else {
				const { run, metricValue, data } = r;
				if (run.modelName === 'climatology') {
					const stops = getStops(metricValue, run.colorIndex);
					const layer = buildRawLayer(data, stops);
					addLayerState(key, { layer, data, stops, isDelta: false });
				} else {
					const climData = climByMetric[metricValue];
					if (climData) {
						const { layer, maxAbs } = buildDeltaLayer(data, climData);
						addLayerState(key, {
							layer,
							data,
							stops: DIVERGING_STOPS,
							isDelta: true,
							deltaMaxAbs: maxAbs,
							climData
						});
					} else {
						// Fallback to raw if clim unavailable
						const stops = getStops(metricValue, run.colorIndex);
						const layer = buildRawLayer(data, stops);
						addLayerState(key, { layer, data, stops, isDelta: false });
					}
				}
			}
		}
		errors = newErrors;
		loading = new Set();

		const loadedVisibleKeys = [...visibleKeys].filter((key) => layers[key]);
		if (loadedVisibleKeys.length === 0) {
			const firstLoadedKey = allKeys.find((key) => layers[key]);
			visibleKeys = firstLoadedKey ? new Set([firstLoadedKey]) : new Set();
			if (firstLoadedKey) {
				layers[firstLoadedKey].layer.setVisible(true);
				layers[firstLoadedKey].layer.setOpacity(opacities[firstLoadedKey] ?? 1);
			}
		} else if (loadedVisibleKeys.length !== visibleKeys.size) {
			visibleKeys = new Set(loadedVisibleKeys);
		}

		const firstVisibleKey = [...visibleKeys].find((key) => layers[key]);
		if (firstVisibleKey) fitToLayer(layers[firstVisibleKey].layer);

		if (Object.values(layers).length > 0) {
			for (const level of visibleBoundaryLevels) untrack(() => loadBoundaryLayer(level));
		}
	}

	// ---- Map setup ---------------------------------------------------------------

	onMount(() => {
		if (!mapContainer) return;
		map = new Map({
			target: mapContainer,
			layers: [new TileLayer({ source: new OSM() })],
			view: new View({ center: fromLonLat([80, 20]), zoom: 4 })
		});

		map.on('pointermove', (e) => {
			if (!map || e.dragging) {
				tooltipVisible = false;
				return;
			}
			const feature = map.forEachFeatureAtPixel(e.pixel, (f) => f);
			if (feature && feature.get('lat') != null) {
				const lat = Number(feature.get('lat'));
				const lon = Number(feature.get('lon'));
				tooltipContent = buildTooltipContent(lat, lon);
				tooltipX = e.pixel[0] + 14;
				tooltipY = e.pixel[1] - 10;
				tooltipVisible = true;
				mapContainer!.style.cursor = 'crosshair';
			} else {
				tooltipVisible = false;
				mapContainer!.style.cursor = '';
			}
		});

		map.on('click', (e) => {
			if (!map) return;
			const feature = map.forEachFeatureAtPixel(e.pixel, (f) => f);
			if (!feature || feature.get('lat') == null) return;
			openCellInspector(Number(feature.get('lat')), Number(feature.get('lon')));
		});
	});

	onDestroy(() => {
		if (map) {
			map.setTarget(undefined);
			map = null;
		}
	});

	$effect(() => {
		// Only trigger on job set or window changes — do NOT read jobs/metrics directly
		// as that would re-run loadAll on every poll even when complete jobs are unchanged.
		if (jobIds && forecastWindow && map) untrack(loadAll);
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
		setTimeout(() => map?.updateSize(), 300);
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

	<!-- Layer panel -->
	<div class="layer-panel" class:collapsed={panelCollapsed}>
		<button class="layer-panel-header" onclick={() => (panelCollapsed = !panelCollapsed)}>
			<span class="layer-panel-title">Layers</span>
			<span class="panel-toggle">{panelCollapsed ? '▸' : '▾'}</span>
		</button>

		{#if !panelCollapsed}
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

			{#each activeRuns as run}
				<div class="run-group" class:clim-group={run.modelName === 'climatology'}>
					<div class="run-header">
						<span class="run-label">{modelDisplayName(run.modelName)}</span>
						{#if run.modelName === 'climatology'}
							<span class="clim-badge">baseline</span>
						{/if}
					</div>

					{#each metrics as m}
						{@const key = layerKey(run.jobId, run.modelName, m.value)}
						{@const isVisible = visibleKeys.has(key)}
						{@const isLoading = loading.has(key)}
						{@const err = errors[key]}
						{@const opacity = opacities[key] ?? 1}

						<div class="layer-item" class:visible={isVisible}>
							<button
								class="layer-row"
								onclick={() => toggleLayer(key)}
								disabled={isLoading || !!err}
							>
								<span class="layer-checkbox" class:checked={isVisible}>
									{#if isVisible}<span class="checkmark">✓</span>{/if}
								</span>
								<span class="layer-label">{m.label}</span>
								{#if isLoading}
									<span class="layer-spinner"></span>
								{:else if err}
									<span class="layer-error" title={err}>✕</span>
								{/if}
							</button>

							{#if isVisible}
								<div class="opacity-row">
									<span class="opacity-icon">◑</span>
									<input
										type="range"
										min="0"
										max="1"
										step="0.05"
										value={opacity}
										oninput={(e) =>
											setOpacity(key, parseFloat((e.target as HTMLInputElement).value))}
										class="opacity-slider"
										style="--pct: {Math.round(opacity * 100)}%"
									/>
									<span class="opacity-value">{Math.round(opacity * 100)}</span>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/each}
		{/if}
	</div>

	{#if tooltipVisible}
		<div class="tooltip" style="left: {tooltipX}px; top: {tooltipY}px">
			{@html tooltipContent}
		</div>
	{/if}

	<!-- Legend: one entry per visible layer -->
	{#if visibleLayers.length > 0}
		<div class="legend">
			{#each visibleLayers as vl, i}
				{@const { modelName: vModel, metric: vMetric } = parseKey(vl.key)}
				{@const gradient = `linear-gradient(to right, ${vl.stops.join(', ')})`}
				{@const displayName = modelDisplayName(vModel)}
				{#if i > 0}<div class="legend-divider"></div>{/if}
				<div class="legend-title">
					{displayName} — {metricLabel(vMetric)}
					{#if vl.isDelta}<span class="legend-delta-badge">Δ vs climatology</span>{/if}
				</div>
				<div class="scale-bar" style="background: {gradient}"></div>
				{#if vl.isDelta && vl.deltaMaxAbs != null}
					<div class="scale-labels">
						<span>−{vl.deltaMaxAbs.toFixed(3)}</span>
						<span class="scale-unit">(better)</span>
						<span>0</span>
						<span class="scale-unit">(worse)</span>
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
	.map-instance :global(.ol-viewport) {
		border-radius: 0.5rem;
	}
	.map-instance :global(.ol-control button) {
		background-color: rgba(255, 255, 255, 0.85);
		color: #333;
		border-radius: 0.25rem;
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
		width: 190px;
		overflow: hidden;
		max-height: calc(100% - 1.5rem);
		overflow-y: auto;
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

	/* ---- Run groups ---- */
	.run-group {
		border-top: 1px solid #e8e8e8;
	}

	.clim-group {
		border-top: 2px solid #e8e8e8;
		background: rgba(0, 0, 0, 0.015);
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

	.clim-badge {
		font-size: 0.52rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: #7a5c9a;
		background: #f3eafa;
		border: 1px solid #d9b8f0;
		padding: 0.05rem 0.35rem;
		border-radius: 0.25rem;
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

	/* ---- Opacity slider ---- */
	.opacity-row {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.05rem 0.5rem 0.35rem;
		background: rgba(0, 0, 0, 0.03);
		box-sizing: border-box;
		width: 100%;
		min-width: 0;
	}
	.opacity-icon {
		font-size: 0.7rem;
		color: #999;
		flex-shrink: 0;
	}

	.opacity-slider {
		flex: 1;
		min-width: 0;
		height: 6px;
		appearance: none;
		background: linear-gradient(
			to right,
			var(--color-accent, #3b82f6) 0%,
			var(--color-accent, #3b82f6) var(--pct, 100%),
			#ddd var(--pct, 100%),
			#ddd 100%
		);
		border-radius: 3px;
		outline: none;
		cursor: pointer;
	}
	.opacity-slider::-webkit-slider-thumb {
		appearance: none;
		width: 13px;
		height: 13px;
		border-radius: 50%;
		background: white;
		border: 2px solid var(--color-accent, #3b82f6);
		cursor: pointer;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
	}
	.opacity-slider::-moz-range-thumb {
		width: 13px;
		height: 13px;
		border-radius: 50%;
		background: white;
		border: 2px solid var(--color-accent, #3b82f6);
		cursor: pointer;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
	}
	.opacity-value {
		font-size: 0.62rem;
		font-family: var(--font-mono);
		color: #555;
		width: 1.8rem;
		text-align: right;
		flex-shrink: 0;
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

	/* ---- Legend ---- */
	.legend {
		position: absolute;
		bottom: 1.5rem;
		right: 1.5rem;
		z-index: 20;
		background: rgba(255, 255, 255, 0.92);
		padding: 0.6rem 0.75rem;
		border-radius: 0.4rem;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
		border: 1px solid #ccc;
		min-width: 160px;
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
