<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import { type Job } from '$lib/api';
	import { getCachedJobGrid } from '$lib/benchmarks.svelte';
	import GridCellInspector from '$lib/components/GridCellInspector.svelte';
	import MetricMapControls from '$lib/components/metric-map/MetricMapControls.svelte';
	import MetricMapLegend from '$lib/components/metric-map/MetricMapLegend.svelte';
	import SwipeComparisonOverlay from '$lib/components/metric-map/SwipeComparisonOverlay.svelte';
	import { BASEMAP_STYLES, type BasemapStyleId } from '$lib/components/metric-map/constants';
	import { BoundaryLayers } from '$lib/components/metric-map/boundaries.svelte';
	import { CellInspector } from '$lib/components/metric-map/cellInspector.svelte';
	import {
		availableModelRuns as getAvailableModelRuns,
		currentLensKey as getCurrentLensKey,
		currentLensKeys as getCurrentLensKeys,
		lensSelectionsEqual,
		normalizeLensSelection as getNormalizedLensSelection,
		swipeRuns as getSwipeRuns,
		type LensSelection
	} from '$lib/components/metric-map/lensSelection';
	import {
		allRawLayerKeys,
		buildClimatologyRun,
		buildDeltaLayerEntries,
		buildModelRuns,
		buildRawLayerEntries,
		computeSharedRanges,
		fetchGridResults,
		indexGridResults
	} from '$lib/components/metric-map/layerPipeline';
	import { modelDisplayName } from '$lib/components/metric-map/mapUi';
	import { buildTooltipContent as formatTooltipContent } from '$lib/components/metric-map/tooltip';
	import type {
		LayerState,
		MetricDef,
		MetricWindowAvailability,
		MetricWindowAvailabilityByJob,
		RunDef,
		WindowDef
	} from '$lib/components/metric-map/types';

	type Props = {
		jobs: Job[]; // complete jobs in the selected run group
		forecastWindow: string;
		metrics: MetricDef[];
		forecastWindows?: WindowDef[];
		metricWindowAvailability?: MetricWindowAvailability;
		metricWindowAvailabilityByJob?: MetricWindowAvailabilityByJob;
		compact?: boolean;
	};
	let {
		jobs,
		forecastWindow,
		metrics,
		forecastWindows,
		metricWindowAvailability,
		metricWindowAvailabilityByJob,
		compact = false
	}: Props = $props();

	let mapContainer = $state<HTMLElement | null>(null);
	let map = $state<maplibregl.Map | null>(null);
	let mapReady = $state(false);
	let selectedBasemap = $state<BasemapStyleId>('carto-dark');

	let layers = $state<Record<string, LayerState>>({});
	let loading = $state<Set<string>>(new Set());
	let errors = $state<Record<string, string>>({});
	let visibleKeys = $state<Set<string>>(new Set());
	let opacities = $state<Record<string, number>>({});
	let activeRuns = $state<RunDef[]>([]);
	let lens = $state<LensSelection>({
		viewMode: 'baseline',
		selectedMetric: '',
		selectedModelJobId: '',
		selectedReferenceJobId: 'climatology',
		selectedWindow: '',
		selectedReferenceWindow: ''
	});
	let swipePosition = $state(50);
	let draggingSwipe = $state(false);
	let loadRequestId = 0;
	const boundaryRegion = $derived(jobs[0]?.region_id ?? jobs[0]?.params?.region);

	const boundaries = new BoundaryLayers(
		() => map,
		() => boundaryRegion
	);
	const cellInspector = new CellInspector();

	let panelCollapsed = $state(false);
	let fullscreen = $state(false);

	let tooltipVisible = $state(false);
	let tooltipX = $state(0);
	let tooltipY = $state(0);
	let tooltipContent = $state('');
	let gridCellHover = $state(false);

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
	const anyLoading = $derived(loading.size > 0);
	const activeWindows = $derived(
		forecastWindows?.length ? forecastWindows : [{ value: forecastWindow, label: forecastWindow }]
	);
	const activeWindowKey = $derived(activeWindows.map((window) => window.value).join(','));

	function metricLabel(metricValue: string) {
		return metrics.find((m) => m.value === metricValue)?.label ?? metricValue;
	}

	function selectedBasemapStyle() {
		return BASEMAP_STYLES.find((style) => style.id === selectedBasemap) ?? BASEMAP_STYLES[0];
	}

	function windowLabelFor(value: string | undefined) {
		if (!value) return '';
		return activeWindows.find((window) => window.value === value)?.label ?? value;
	}

	function metricAvailabilityForJob(jobId: string): MetricWindowAvailability | undefined {
		return metricWindowAvailabilityByJob?.[jobId] ?? metricWindowAvailability;
	}

	function windowsForMetric(metric: string, jobId = lens.selectedModelJobId) {
		const availability = metricAvailabilityForJob(jobId);
		return activeWindows.filter(
			(window) => !availability || availability[metric]?.includes(window.value)
		);
	}

	function metricsForWindow(window: string, jobId = lens.selectedModelJobId) {
		const availability = metricAvailabilityForJob(jobId);
		return metrics.filter(
			(metric) => !availability || availability[metric.value]?.includes(window)
		);
	}

	function selectMetric(value: string) {
		lens.selectedMetric = value;
		const availableWindows = windowsForMetric(value);
		if (
			availableWindows.length > 0 &&
			!availableWindows.some((window) => window.value === lens.selectedWindow)
		) {
			lens.selectedWindow = availableWindows[0].value;
		}
		if (
			availableWindows.length > 0 &&
			!availableWindows.some((window) => window.value === lens.selectedReferenceWindow)
		) {
			lens.selectedReferenceWindow = lens.selectedWindow;
		}
	}

	function selectWindow(value: string) {
		lens.selectedWindow = value;
		const availableMetrics = metricsForWindow(value);
		if (
			availableMetrics.length > 0 &&
			!availableMetrics.some((metric) => metric.value === lens.selectedMetric)
		) {
			lens.selectedMetric = availableMetrics[0].value;
		}
		const availableReferenceWindows = windowsForMetric(lens.selectedMetric);
		if (
			availableReferenceWindows.length > 0 &&
			!availableReferenceWindows.some((window) => window.value === lens.selectedReferenceWindow)
		) {
			lens.selectedReferenceWindow = lens.selectedWindow;
		}
	}

	function selectReferenceWindow(value: string) {
		const availableWindows = windowsForMetric(lens.selectedMetric);
		lens.selectedReferenceWindow = availableWindows.some((window) => window.value === value)
			? value
			: (availableWindows[0]?.value ?? lens.selectedWindow);
	}

	function lensSelection(): LensSelection {
		return { ...lens };
	}

	function setLensSelection(selection: LensSelection) {
		lens = selection;
	}

	function normalizeLensSelection() {
		const current = lensSelection();
		const normalized = getNormalizedLensSelection(current, {
			activeRuns,
			activeWindows,
			forecastWindow,
			metrics,
			metricWindowAvailability,
			metricWindowAvailabilityByJob
		});
		if (!lensSelectionsEqual(current, normalized)) setLensSelection(normalized);
		return normalized;
	}

	function availableModelRuns() {
		return getAvailableModelRuns(activeRuns);
	}

	function swipeRuns() {
		return getSwipeRuns(normalizeLensSelection(), activeRuns);
	}

	function currentLensKey() {
		return getCurrentLensKey(normalizeLensSelection(), activeRuns);
	}

	function currentLensKeys() {
		return getCurrentLensKeys(normalizeLensSelection(), activeRuns);
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
		if (lens.viewMode !== 'swipe' || keys.length !== 2) return;
		const splitLon = swipeLongitude();
		if (splitLon == null) return;
		const [leftKey, rightKey] = keys;
		const leftLayer = layers[leftKey]?.layerId;
		const rightLayer = layers[rightKey]?.layerId;
		if (leftLayer && map.getLayer(leftLayer))
			map.setFilter(leftLayer, ['<=', ['get', 'lon'], splitLon]);
		if (rightLayer && map.getLayer(rightLayer))
			map.setFilter(rightLayer, ['>', ['get', 'lon'], splitLon]);
	}

	function applyLensSelection(fit = false) {
		const keys = currentLensKeys();
		const nextVisibleKeys = new Set(keys.filter((key) => layers[key]));
		for (const [layerKey, state] of Object.entries(layers)) {
			if (!map?.getLayer(state.layerId)) continue;
			map.setLayoutProperty(
				state.layerId,
				'visibility',
				nextVisibleKeys.has(layerKey) ? 'visible' : 'none'
			);
			if (nextVisibleKeys.has(layerKey)) {
				map.setPaintProperty(state.layerId, 'fill-opacity', opacities[layerKey] ?? 1);
			}
		}
		visibleKeys = nextVisibleKeys;
		updateSwipeFilters();
		const firstKey = keys.find((key) => layers[key]);
		if (fit && firstKey) fitToLayer(layers[firstKey]);
	}

	function buildTooltipContent(lat: number, lon: number): string {
		return formatTooltipContent({
			lat,
			lon,
			visibleKeys,
			layers,
			metricLabel,
			windowLabelFor
		});
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

	// ---- Full load (called when jobs or window changes) --------------------------

	async function loadAll() {
		if (!map || !mapReady) return;
		const requestId = ++loadRequestId;
		const previousVisibleKeys = new Set(visibleKeys);
		const previousOpacities = { ...opacities };

		for (const { layerId, sourceId } of Object.values(layers)) removeMapLayer(layerId, sourceId);
		boundaries.clearFromMap();
		layers = {};
		errors = {};
		loading = new Set();

		if (jobs.length === 0 || metrics.length === 0) {
			activeRuns = [];
			visibleKeys = new Set();
			opacities = {};
			return;
		}

		const modelRuns = buildModelRuns(jobs);
		const climRun = buildClimatologyRun(jobs);
		if (!climRun) return;
		setLensSelection(
			getNormalizedLensSelection(lensSelection(), {
				activeRuns: [...modelRuns, climRun],
				activeWindows,
				forecastWindow,
				metrics,
				metricWindowAvailability,
				metricWindowAvailabilityByJob
			})
		);

		const fetchRuns = [...modelRuns, climRun];
		const allKeys = allRawLayerKeys(
			fetchRuns,
			activeWindows,
			metrics,
			metricWindowAvailability,
			metricWindowAvailabilityByJob
		);
		visibleKeys = new Set([...previousVisibleKeys].filter((key) => allKeys.includes(key)));
		opacities = Object.fromEntries(allKeys.map((key) => [key, previousOpacities[key] ?? 1]));
		loading = new Set(allKeys);

		const results = await fetchGridResults(
			fetchRuns,
			activeWindows,
			metrics,
			metricWindowAvailability,
			metricWindowAvailabilityByJob,
			getCachedJobGrid
		);
		if (requestId !== loadRequestId) return;

		const { dataByRunMetric, hasClimatology } = indexGridResults(results);
		activeRuns = hasClimatology ? [...modelRuns, climRun] : modelRuns;

		const sharedRangeByMetric = computeSharedRanges(
			activeRuns,
			activeWindows,
			metrics,
			dataByRunMetric,
			metricWindowAvailability,
			metricWindowAvailabilityByJob
		);
		const rawLayers = buildRawLayerEntries(results, sharedRangeByMetric);
		for (const { key, state } of rawLayers.entries) addLayerState(key, state);

		const deltaLayers = buildDeltaLayerEntries(
			modelRuns,
			activeRuns,
			activeWindows,
			metrics,
			dataByRunMetric,
			metricWindowAvailability,
			metricWindowAvailabilityByJob
		);
		for (const { key, state } of deltaLayers) {
			addLayerState(key, state);
			opacities = { ...opacities, [key]: previousOpacities[key] ?? 1 };
		}
		errors = rawLayers.errors;
		loading = new Set();

		applyLensSelection(true);
		const firstVisibleKey = [...visibleKeys][0];
		if (firstVisibleKey) fitToLayer(layers[firstVisibleKey]);

		if (Object.values(layers).length > 0) {
			untrack(() => boundaries.reloadVisible());
		}
	}

	// ---- Map setup ---------------------------------------------------------------

	onMount(() => {
		if (!mapContainer) return;
		map = new maplibregl.Map({
			container: mapContainer,
			style: selectedBasemapStyle().url,
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
			if (lens.viewMode === 'swipe') updateSwipeFilters();
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
				gridCellHover = true;
			} else {
				tooltipVisible = false;
				gridCellHover = false;
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
			cellInspector.open(lat, lon);
		});

		map.on('mouseout', () => {
			tooltipVisible = false;
			gridCellHover = false;
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
		if (jobIds && forecastWindow && activeWindowKey && map && mapReady) untrack(loadAll);
	});

	$effect(() => {
		const style = selectedBasemapStyle();
		if (!map || !mapReady) return;
		map.once('style.load', () => {
			untrack(loadAll);
		});
		map.setStyle(style.url);
	});

	$effect(() => {
		lens.selectedMetric;
		lens.selectedModelJobId;
		lens.selectedReferenceJobId;
		lens.selectedWindow;
		lens.selectedReferenceWindow;
		lens.viewMode;
		swipePosition;
		if (map && mapReady && Object.keys(layers).length > 0) {
			untrack(() => applyLensSelection(false));
		}
	});

	$effect(() => {
		const cell = cellInspector.selected;
		const window = lens.selectedWindow;
		const activeJobIds = jobIds;
		if (cell && activeJobIds && window) {
			const jobsSnapshot = untrack(() => jobs);
			untrack(() => cellInspector.load(window, jobsSnapshot));
		}
	});

	$effect(() => {
		fullscreen;
		setTimeout(() => map?.resize(), 300);
	});
</script>

<div class="map-root" class:fullscreen class:compact>
	<div class="map-stage" class:grid-cell-hover={gridCellHover}>
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

		{#if tooltipVisible}
			<div class="tooltip" style="left: {tooltipX}px; top: {tooltipY}px">
				{@html tooltipContent}
			</div>
		{/if}

		{#if lens.viewMode === 'swipe' && visibleLayers.length === 2}
			{@const currentSwipeRuns = swipeRuns()}
			<SwipeComparisonOverlay
				left={currentSwipeRuns?.left ?? null}
				right={currentSwipeRuns?.right ?? null}
				selectedWindow={lens.selectedWindow}
				selectedReferenceWindow={lens.selectedReferenceWindow}
				{swipePosition}
				{draggingSwipe}
				{windowLabelFor}
				onStartDrag={startSwipeDrag}
				onKeyboardMove={moveSwipeWithKeyboard}
			/>
		{/if}

		<MetricMapLegend {visibleLayers} viewMode={lens.viewMode} {metricLabel} {windowLabelFor} />

		{#if boundaries.visibleLayers.length > 0}
			<div class="boundary-attribution">
				Boundaries: geoBoundaries gbOpen
				{#each boundaries.visibleLayers as boundaryLayer, i}
					{#if i === 0}({:else};
					{/if}{boundaryLayer.label}{#if i === boundaries.visibleLayers.length - 1}){/if}
				{/each}
			</div>
		{/if}

		{#if cellInspector.selected}
			<GridCellInspector
				cell={cellInspector.selected}
				forecastWindow={lens.selectedWindow}
				{metrics}
				results={cellInspector.results}
				loading={cellInspector.loading}
				error={cellInspector.error}
				onclose={() => cellInspector.close()}
			/>
		{/if}
	</div>

	<MetricMapControls
		{panelCollapsed}
		{metrics}
		{activeWindows}
		{metricWindowAvailability}
		{metricWindowAvailabilityByJob}
		{activeRuns}
		availableModelRuns={availableModelRuns()}
		selectedMetric={lens.selectedMetric}
		selectedWindow={lens.selectedWindow}
		selectedModelJobId={lens.selectedModelJobId}
		selectedReferenceJobId={lens.selectedReferenceJobId}
		selectedReferenceWindow={lens.selectedReferenceWindow}
		{selectedBasemap}
		viewMode={lens.viewMode}
		visibleBoundaryLevels={boundaries.visibleLevels}
		boundaryLoading={boundaries.loading}
		boundaryErrors={boundaries.errors}
		onTogglePanel={() => (panelCollapsed = !panelCollapsed)}
		onSelectMetric={selectMetric}
		onSelectWindow={selectWindow}
		onSelectModel={(value) => (lens.selectedModelJobId = value)}
		onSelectViewMode={(value) => {
			lens.viewMode = value;
			if (value === 'baseline') lens.selectedReferenceJobId = 'climatology';
		}}
		onSelectReferenceJob={(value) => (lens.selectedReferenceJobId = value)}
		onSelectReferenceWindow={selectReferenceWindow}
		onSelectBasemap={(value) => (selectedBasemap = value)}
		onToggleBoundary={(level) => boundaries.toggle(level)}
	/>
</div>

<style>
	/* ---- Map root ---- */
	.map-root {
		position: relative;
		display: flex;
		flex-direction: column;
		width: 100%;
		border: 1px solid #d8d0c2;
		border-radius: 0.5rem;
		overflow: hidden;
		background: rgba(255, 253, 248, 0.98);
		box-shadow: 0 0.45rem 1.6rem rgba(43, 36, 24, 0.08);
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

	.map-root.fullscreen .map-instance {
		height: 100%;
	}

	.map-root.compact .map-stage {
		height: clamp(18rem, 45vh, 27rem);
	}

	.map-stage {
		position: relative;
		order: 1;
		flex: none;
		height: clamp(22rem, 60vh, 37.5rem);
		overflow: hidden;
		background: #101418;
	}

	.map-root.fullscreen .map-stage {
		flex: 1;
		height: auto;
		min-height: 0;
	}

	:global(.map-root.fullscreen.obscured-by-lightbox) {
		z-index: 1;
	}

	.map-instance {
		width: 100%;
		height: 100%;
		background: #101418;
	}
	.map-instance :global(.maplibregl-canvas-container),
	.map-instance :global(.maplibregl-canvas) {
		width: 100% !important;
		height: 100% !important;
	}
	.map-instance :global(.maplibregl-canvas) {
		border-radius: 0;
	}
	.map-stage.grid-cell-hover :global(.maplibregl-canvas-container),
	.map-stage.grid-cell-hover :global(.maplibregl-canvas) {
		cursor: crosshair !important;
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

	/* ---- Tooltip ---- */
	.tooltip {
		position: absolute;
		z-index: 30;
		background: rgba(255, 255, 255, 0.95);
		backdrop-filter: blur(6px);
		border: 1px solid rgba(31, 43, 52, 0.14);
		border-radius: 0.4rem;
		padding: 0.4rem 0.6rem;
		font-size: 0.75rem;
		font-family: var(--font-body);
		color: #1f2b34;
		box-shadow: 0 2px 8px rgba(3, 14, 25, 0.18);
		pointer-events: none;
		line-height: 1.5;
		min-width: 10rem;
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
		color: #627174;
		margin-bottom: 0.1rem;
	}

	.tooltip :global(.tt-metric) {
		display: block;
		font-size: 0.73rem;
		color: #1f2b34;
		font-family: var(--font-mono);
	}

	.tooltip :global(.tt-delta) {
		font-size: 0.68rem;
		color: #627174;
	}

	:global(body.figure-lightbox-open .map-root) .boundary-attribution,
	:global(body.figure-lightbox-open .map-root) .fullscreen-btn,
	:global(body.figure-lightbox-open .map-root) .status-overlay,
	:global(body.figure-lightbox-open .map-root) .tooltip,
	:global(.map-root.fullscreen.obscured-by-lightbox) .boundary-attribution,
	:global(.map-root.fullscreen.obscured-by-lightbox) .fullscreen-btn,
	:global(.map-root.fullscreen.obscured-by-lightbox) .status-overlay,
	:global(.map-root.fullscreen.obscured-by-lightbox) .tooltip {
		display: none;
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
