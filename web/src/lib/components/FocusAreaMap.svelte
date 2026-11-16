<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import * as maplibregl from 'maplibre-gl';
	import 'maplibre-gl/dist/maplibre-gl.css';
	import '$lib/maplibre-worker';
	import type { FeatureCollection } from 'geojson';
	import { BASEMAP_STYLES } from '$lib/basemaps';
	import {
		isFocusUnits,
		type BboxExtent,
		type FocusAreaValue,
		type FocusUnitLevel
	} from '$lib/api/jobs';
	import { getCountries, getPlaceBoundary, type Country } from '$lib/api/places';
	import { boxBetween, boxOutline, isDegenerate, shapeBounds } from '$lib/geo/bbox';
	import { pickedPlaces, toggled, wholeCountry } from '$lib/places';

	interface Props {
		value: FocusAreaValue | null;
		/** Bounding box to frame when nothing is drawn yet. */
		extent?: BboxExtent | null;
		/** Where places are picked until the user chooses a country, by its three-letter code. */
		country?: string | null;
		onchange: (value: FocusAreaValue | null) => void;
	}

	const { value, extent = null, country: defaultCountry = null, onchange }: Props = $props();

	type Mode = 'all' | 'box' | 'units';
	const modes: { id: Mode; label: string }[] = [
		{ id: 'all', label: 'All of the data' },
		{ id: 'box', label: 'Draw a box' },
		{ id: 'units', label: 'Pick places' }
	];
	// Larger places first: a broad area is a handful of states, not hundreds of districts.
	const UNIT_LEVELS: { id: FocusUnitLevel; label: string }[] = [
		{ id: 'adm0', label: 'Whole country' },
		{ id: 'adm1', label: 'States & regions' },
		{ id: 'adm2', label: 'Districts & zones' }
	];

	const SOURCE = 'focus-area';
	const COUNTRY = 'focus-country';
	const ZONES = 'focus-zones';
	const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
	let container = $state<HTMLDivElement | null>(null);
	let map: maplibregl.Map | null = null;
	let mapReady = $state(false);
	let drawing = $state(false);
	let anchor: maplibregl.LngLat | null = null;
	let countryShape = $state<FeatureCollection | null>(null);
	let zones = $state<FeatureCollection | null>(null);
	let zonesFailed = $state(false);
	let mode = $state<Mode>('all');
	let unitLevel = $state<FocusUnitLevel>('adm1');
	let countries = $state<Country[]>([]);
	/** A country chosen here, or named by picked places; undefined until there is one. */
	let chosenCountry = $state<string | null | undefined>(undefined);
	/** The country places are picked in, by its three-letter code. */
	const country = $derived(chosenCountry === undefined ? defaultCountry : chosenCountry);

	const box = $derived(value && !isFocusUnits(value) ? value : null);
	const picked = $derived(isFocusUnits(value) ? value.units : []);

	function setData(source: string, data: FeatureCollection) {
		(map?.getSource(source) as maplibregl.GeoJSONSource | undefined)?.setData(data);
	}

	function fitTo(area: BboxExtent | null) {
		if (!area || !map) return;
		map.fitBounds(
			[
				[area.lon_min, area.lat_min],
				[area.lon_max, area.lat_max]
			],
			{ padding: 24, duration: 0 }
		);
	}

	function frame() {
		const countryExtent = countryShape ? shapeBounds(countryShape) : null;
		// The host's extent is its data's; a country chosen here is somewhere else.
		const chosenHere = chosenCountry !== undefined;
		fitTo(box ?? (chosenHere ? countryExtent : null) ?? extent ?? countryExtent);
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

	function setMode(next: Mode) {
		stopDrawing();
		mode = next;
		// The three choices are exclusive: leaving a mode discards its selection.
		if (next === 'all' || (next === 'box') === isFocusUnits(value)) {
			if (value) onchange(null);
		}
	}

	// Each level has its own areas to load, so switching drops the loaded ones.
	function showLevel(next: FocusUnitLevel) {
		if (next === unitLevel) return;
		unitLevel = next;
		zones = null;
		zonesFailed = false;
	}

	function countryOf(iso: string | null): Country | null {
		return countries.find((candidate) => candidate.iso === iso) ?? null;
	}

	// A selection is one level's places, so changing level starts it over. A whole
	// country is a selection in itself.
	function setUnitLevel(next: FocusUnitLevel) {
		if (next === unitLevel) return;
		showLevel(next);
		const whole = next === 'adm0' ? countryOf(country) : null;
		if (whole) onchange(wholeCountry(whole));
		else if (isFocusUnits(value)) onchange(null);
	}

	// Places belong to one country, so choosing another starts the selection over.
	function setCountry(iso: string) {
		chosenCountry = iso || null;
		const whole = unitLevel === 'adm0' ? countryOf(chosenCountry) : null;
		if (whole) onchange(wholeCountry(whole));
		else if (isFocusUnits(value)) onchange(null);
	}

	function zoneName(feature: maplibregl.MapGeoJSONFeature): string | null {
		const name = feature.id ?? feature.properties?.shapeName;
		return typeof name === 'string' && name ? name : null;
	}

	function togglePicked(name: string) {
		onchange(pickedPlaces(unitLevel, toggled(picked, name), country));
	}

	function clearSelection() {
		onchange(null);
	}

	/** Fill the viewport; the map keeps its instance and just re-measures. */
	let expanded = $state(false);
	async function setExpanded(next: boolean) {
		expanded = next;
		await tick();
		map?.resize();
		if (!value) frame();
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
			if (!map) return;
			// Country first, then pickable areas, then the box, so each paints above the last.
			map.addSource(COUNTRY, { type: 'geojson', data: countryShape ?? EMPTY });
			map.addLayer({
				id: `${COUNTRY}-fill`,
				type: 'fill',
				source: COUNTRY,
				paint: { 'fill-color': '#0f766e', 'fill-opacity': 0.12 }
			});
			map.addLayer({
				id: `${COUNTRY}-line`,
				type: 'line',
				source: COUNTRY,
				paint: { 'line-color': '#0f766e', 'line-width': 1.5, 'line-opacity': 0.8 }
			});
			// Feature state needs an id per area; the name doubles as one.
			map.addSource(ZONES, { type: 'geojson', data: zones ?? EMPTY, promoteId: 'shapeName' });
			map.addLayer({
				id: `${ZONES}-fill`,
				type: 'fill',
				source: ZONES,
				paint: {
					'fill-color': '#2f6fed',
					'fill-opacity': ['case', ['boolean', ['feature-state', 'selected'], false], 0.45, 0.04]
				}
			});
			map.addLayer({
				id: `${ZONES}-line`,
				type: 'line',
				source: ZONES,
				paint: { 'line-color': '#2f6fed', 'line-width': 0.8, 'line-opacity': 0.7 }
			});
			map.addSource(SOURCE, { type: 'geojson', data: boxOutline(box) });
			map.addLayer({
				id: `${SOURCE}-fill`,
				type: 'fill',
				source: SOURCE,
				paint: { 'fill-color': '#2f6fed', 'fill-opacity': 0.2 }
			});
			map.addLayer({
				id: `${SOURCE}-line`,
				type: 'line',
				source: SOURCE,
				paint: { 'line-color': '#2f6fed', 'line-width': 2 }
			});
			map.on('click', `${ZONES}-fill`, (e) => {
				if (mode !== 'units') return;
				const name = e.features?.[0] ? zoneName(e.features[0]) : null;
				if (name) togglePicked(name);
			});
			map.on('mouseenter', `${ZONES}-fill`, () => {
				if (mode === 'units') map?.getCanvas().style.setProperty('cursor', 'pointer');
			});
			map.on('mouseleave', `${ZONES}-fill`, () => {
				if (mode === 'units') map?.getCanvas().style.removeProperty('cursor');
			});
			mapReady = true;
			frame();
			// MapLibre opens the compact attribution on first render; start it folded.
			const attrib = container?.querySelector<HTMLDetailsElement>('.maplibregl-ctrl-attrib');
			attrib?.classList.remove('maplibregl-compact-show');
			attrib?.removeAttribute('open');
		});
		map.on('mousedown', (e) => {
			if (!drawing) return;
			e.preventDefault();
			anchor = e.lngLat;
		});
		map.on('mousemove', (e) => {
			if (anchor) setData(SOURCE, boxOutline(boxBetween(anchor, e.lngLat)));
		});
		map.on('mouseup', (e) => {
			if (!anchor) return;
			const drawn = boxBetween(anchor, e.lngLat);
			stopDrawing();
			if (isDegenerate(drawn)) {
				setData(SOURCE, boxOutline(box));
				return;
			}
			onchange(drawn);
		});
	});

	onDestroy(() => map?.remove());

	// A value set from outside (chat, a reloaded config) decides the mode, and the
	// country when its places name one.
	$effect(() => {
		if (isFocusUnits(value)) {
			mode = 'units';
			const level = value.level;
			const iso = value.iso;
			untrack(() => {
				showLevel(level);
				if (iso) chosenCountry = iso;
			});
		} else if (value) mode = 'box';
	});

	// The list is only missed by the country choice; places already picked still show.
	onMount(() => {
		getCountries()
			.then((listed) => (countries = listed))
			.catch(() => {});
	});

	// The country's border, when the boundary service knows it. A miss just leaves
	// the basemap, so the map stays usable without boundary data.
	$effect(() => {
		const iso = country;
		countryShape = null;
		zones = null;
		zonesFailed = false;
		if (!iso) return;
		let stale = false;
		getPlaceBoundary(iso, 'adm0')
			.then(({ geojson }) => {
				if (!stale && isFeatureCollection(geojson)) countryShape = geojson;
			})
			.catch(() => {});
		return () => {
			stale = true;
		};
	});

	// Pickable places load on demand, the first time the mode calls for them.
	$effect(() => {
		const level = unitLevel;
		const iso = country;
		if (mode !== 'units' || !iso || zones || zonesFailed) return;
		let stale = false;
		getPlaceBoundary(iso, level)
			.then(({ geojson }) => {
				if (stale || !isFeatureCollection(geojson)) return;
				zones = geojson;
				fitTo(shapeBounds(geojson));
			})
			.catch(() => {
				if (!stale) zonesFailed = true;
			});
		return () => {
			stale = true;
		};
	});

	function isFeatureCollection(candidate: unknown): candidate is FeatureCollection {
		return (
			typeof candidate === 'object' &&
			candidate != null &&
			(candidate as { type?: unknown }).type === 'FeatureCollection' &&
			Array.isArray((candidate as { features?: unknown }).features)
		);
	}

	$effect(() => {
		if (mapReady) setData(SOURCE, boxOutline(box));
	});

	// The country highlight means "this is what gets scored", so it yields to any
	// selection and returns when the selection is cleared.
	$effect(() => {
		if (!mapReady) return;
		setData(COUNTRY, value ? EMPTY : (countryShape ?? EMPTY));
		if (!value) frame();
	});

	$effect(() => {
		if (!mapReady || !map) return;
		setData(ZONES, zones ?? EMPTY);
		const visibility = mode === 'units' ? 'visible' : 'none';
		map.setLayoutProperty(`${ZONES}-fill`, 'visibility', visibility);
		map.setLayoutProperty(`${ZONES}-line`, 'visibility', visibility);
		map.removeFeatureState({ source: ZONES });
		for (const name of picked) map.setFeatureState({ source: ZONES, id: name }, { selected: true });
	});
</script>

<svelte:window
	onkeydown={(event) => {
		if (event.key === 'Escape' && expanded) void setExpanded(false);
	}}
/>

<div class="focus-area" class:expanded>
	<div class="toolbar">
		<div class="modes" role="group" aria-label="Area of interest">
			{#each modes as option (option.id)}
				<button
					type="button"
					class:active={mode === option.id}
					aria-pressed={mode === option.id}
					onclick={() => setMode(option.id)}
				>
					{option.label}
				</button>
			{/each}
		</div>
		{#if mode === 'units'}
			<select
				class="country"
				aria-label="Country to pick places in"
				value={country ?? ''}
				onchange={(event) => setCountry(event.currentTarget.value)}
			>
				<option value="" disabled>Choose a country</option>
				{#each countries as listed (listed.iso)}
					<option value={listed.iso}>{listed.name}</option>
				{/each}
			</select>
			<div class="modes" role="group" aria-label="Kind of place to pick">
				{#each UNIT_LEVELS as option (option.id)}
					<button
						type="button"
						class:active={unitLevel === option.id}
						aria-pressed={unitLevel === option.id}
						onclick={() => setUnitLevel(option.id)}
					>
						{option.label}
					</button>
				{/each}
			</div>
		{/if}
		<button
			type="button"
			class="expand"
			title={expanded ? 'Back to the panel' : 'Open a larger map'}
			onclick={() => setExpanded(!expanded)}
		>
			{expanded ? 'Close' : 'Expand'}
		</button>
	</div>
	<div class="map-frame">
		<div class="map" bind:this={container}></div>
		{#if drawing}
			<p class="map-hint">Click and drag to draw the box</p>
		{:else if mode === 'units' && picked.length === 0}
			<p class="map-hint">
				{#if zonesFailed}
					Couldn't load areas.
					<button type="button" class="retry" onclick={() => (zonesFailed = false)}> Retry </button>
				{:else}
					{#if !country}
						Choose a country to pick places in
					{:else}
						{zones ? 'Click places to add them' : 'Loading places…'}
					{/if}
				{/if}
			</p>
		{/if}
	</div>
	<div class="controls">
		{#if mode === 'box'}
			<button
				type="button"
				class="draw"
				class:cancel={drawing}
				title={box ? 'Replace the current box with a new one' : 'Limit scoring to a box you draw'}
				onclick={drawing ? stopDrawing : startDrawing}
			>
				{drawing ? 'Cancel' : box ? 'Redraw box' : 'Draw box'}
			</button>
			{#if box}
				<button type="button" class="clear" title="Remove the box" onclick={clearSelection}>
					Clear
				</button>
				<small>
					{box.lat_min}° to {box.lat_max}° lat, {box.lon_min}° to {box.lon_max}° lon
				</small>
			{:else if !drawing}
				<small>No box yet, so all of the data is scored</small>
			{/if}
		{:else if mode === 'units'}
			{#if picked.length}
				<button type="button" class="clear" title="Deselect every place" onclick={clearSelection}>
					Clear
				</button>
				<small>{picked.length} selected: {picked.join(', ')}</small>
			{:else}
				<small>Click a place on the map to add it; click again to remove it</small>
			{/if}
		{:else}
			<small>All of the data is scored</small>
		{/if}
	</div>
</div>

<style>
	.focus-area {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.focus-area.expanded {
		position: fixed;
		inset: 0;
		z-index: 1000;
		padding: 1rem clamp(1rem, 4vw, 3rem);
		background: var(--color-surface);
	}

	.toolbar {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: center;
		gap: 0.5rem;
	}

	.expand {
		border: 1px solid var(--color-border);
		border-radius: 0.4rem;
		padding: 0.3rem 0.7rem;
		background: var(--color-bg);
		color: var(--color-text);
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}

	.expanded .map-frame {
		display: flex;
		flex: 1;
		min-block-size: 0;
	}

	.expanded .map {
		aspect-ratio: auto;
		block-size: 100%;
	}

	.modes {
		display: flex;
		gap: 0.25rem;
		padding: 0.2rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
		inline-size: fit-content;
	}

	.country {
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		padding: 0.4rem 0.6rem;
		background: var(--color-bg);
		color: var(--color-text);
		font: inherit;
		font-size: 0.85rem;
		max-inline-size: 100%;
	}

	.modes button {
		border: 0;
		border-radius: 0.35rem;
		padding: 0.3rem 0.7rem;
		background: transparent;
		color: var(--color-text-muted);
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}

	.modes button.active {
		background: var(--color-surface-raised);
		color: var(--color-text);
		box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
	}

	.map-frame {
		position: relative;
	}

	.map {
		inline-size: 100%;
		aspect-ratio: 16 / 9;
		min-block-size: 12rem;
		border-radius: 0.375rem;
		overflow: hidden;
	}

	.map-hint {
		position: absolute;
		inset-block-start: 0.5rem;
		inset-inline: 0;
		margin: 0 auto;
		inline-size: fit-content;
		padding: 0.3rem 0.7rem;
		border-radius: 999px;
		background: var(--color-surface-raised);
		color: var(--color-text);
		font-size: 0.85rem;
		font-weight: 600;
		pointer-events: none;
	}

	.retry {
		padding: 0;
		border: none;
		background: none;
		color: var(--color-accent);
		font: inherit;
		text-decoration: underline;
		cursor: pointer;
		pointer-events: auto;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		min-block-size: 1.8rem;
	}

	.controls button {
		border-radius: 0.4rem;
		padding: 0.3rem 0.7rem;
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}

	.draw {
		border: 0;
		background: var(--color-accent);
		color: white;
	}

	.draw:hover {
		background: var(--color-accent-hover);
	}

	.draw.cancel,
	.clear {
		border: 1px solid var(--color-border);
		background: var(--color-bg);
		color: var(--color-text);
	}

	.controls small {
		color: var(--color-text-muted);
	}
</style>
