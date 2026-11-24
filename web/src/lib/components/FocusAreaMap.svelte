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
	import {
		afterToggling,
		countryNamed,
		countryOfSelection,
		matchingNames,
		pickedAt,
		wholeCountry
	} from '$lib/places';
	import { studyAreaWords } from '$lib/workspace/study-area';

	interface Props {
		value: FocusAreaValue | null;
		/** Bounding box to frame when nothing is chosen yet. */
		extent?: BboxExtent | null;
		/** The country the data is about, offered as the first choice, by its three-letter code. */
		country?: string | null;
		/** What choosing no area means here. */
		unsetLabel?: string;
		onchange: (value: FocusAreaValue | null) => void;
	}

	const {
		value,
		extent = null,
		country: suggestedIso = null,
		unsetLabel = 'Everywhere the chosen data covers',
		onchange
	}: Props = $props();

	type View = 'place' | 'box';
	// Larger places first: a broad area is a handful of states, not hundreds of districts.
	const LEVELS: { id: FocusUnitLevel; label: string }[] = [
		{ id: 'adm0', label: 'Whole country' },
		{ id: 'adm1', label: 'States & regions' },
		{ id: 'adm2', label: 'Districts & zones' }
	];
	const COUNTRY_LIST_ID = `focus-countries-${Math.random().toString(36).slice(2)}`;

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
	let view = $state<View>('place');
	let level = $state<FocusUnitLevel>('adm0');
	let countries = $state<Country[]>([]);
	let countryText = $state('');
	let placeQuery = $state('');

	const box = $derived(value && !isFocusUnits(value) ? value : null);
	const chosenIso = $derived(isFocusUnits(value) ? (value.iso ?? suggestedIso) : null);
	/** The country chosen, else the one the data is about, which the map shows as context. */
	const shownIso = $derived(chosenIso ?? suggestedIso);
	// The last whole country seen, so narrowing it down keeps it even without the country list.
	let lastWhole = $state<Country | null>(null);
	const chosenCountry = $derived(
		countryOf(chosenIso) ?? (lastWhole?.iso === chosenIso ? lastWhole : null)
	);
	const suggested = $derived(value ? null : countryOf(suggestedIso));
	const picked = $derived(pickedAt(value, level));
	const placeNames = $derived(zoneNames(zones));
	const shownPlaces = $derived(matchingNames(placeNames, placeQuery));
	const narrowing = $derived(view === 'place' && chosenIso !== null && level !== 'adm0');

	function countryOf(iso: string | null): Country | null {
		return countries.find((candidate) => candidate.iso === iso) ?? null;
	}

	function zoneNames(shapes: FeatureCollection | null): string[] {
		const names = (shapes?.features ?? []).map((feature) => feature.properties?.shapeName);
		return [...new Set(names.filter((name): name is string => typeof name === 'string' && !!name))];
	}

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
		fitTo(box ?? (chosenIso ? countryExtent : null) ?? extent ?? countryExtent);
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

	/** A box and places are different kinds of area, so moving between them starts over. */
	function showView(next: View) {
		stopDrawing();
		view = next;
		if (value) onchange(null);
	}

	function chooseCountry(country: Country) {
		level = 'adm0';
		placeQuery = '';
		onchange(wholeCountry(country));
	}

	/**
	 * A country is taken once the entry is finished (picked from the list, Enter, or
	 * leaving the box), so "Niger" is not chosen on the way to typing "Nigeria".
	 */
	function countryEntered(typed: string) {
		if (!typed.trim()) return clearCountry();
		const named = countryNamed(countries, typed);
		if (!named) countryText = chosenCountry?.name ?? '';
		else if (named.iso !== chosenIso) chooseCountry(named);
	}

	/** Picking from the suggestions arrives as a whole replacement, not as typing. */
	function countryInput(event: Event & { currentTarget: HTMLInputElement }) {
		countryText = event.currentTarget.value;
		const fromList = !(event instanceof InputEvent) || event.inputType === 'insertReplacementText';
		if (fromList) countryEntered(countryText);
	}

	function clearCountry() {
		countryText = '';
		level = 'adm0';
		onchange(null);
	}

	// Places are one level's, so moving to another level goes back to the whole country.
	function showLevel(next: FocusUnitLevel) {
		if (next === level) return;
		level = next;
		zones = null;
		zonesFailed = false;
		placeQuery = '';
		if (chosenCountry && isFocusUnits(value) && value.level !== 'adm0') {
			onchange(wholeCountry(chosenCountry));
		}
	}

	function togglePlace(name: string) {
		onchange(afterToggling(value, level, name, chosenCountry));
	}

	function zoneName(feature: maplibregl.MapGeoJSONFeature): string | null {
		const name = feature.id ?? feature.properties?.shapeName;
		return typeof name === 'string' && name ? name : null;
	}

	/** Fill the viewport; the map keeps its instance and just re-measures. */
	let expanded = $state(false);
	async function setExpanded(next: boolean) {
		expanded = next;
		await tick();
		map?.resize();
		frame();
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
				if (!narrowing) return;
				const name = e.features?.[0] ? zoneName(e.features[0]) : null;
				if (name) togglePlace(name);
			});
			map.on('mouseenter', `${ZONES}-fill`, () => {
				if (narrowing) map?.getCanvas().style.setProperty('cursor', 'pointer');
			});
			map.on('mouseleave', `${ZONES}-fill`, () => {
				if (narrowing) map?.getCanvas().style.removeProperty('cursor');
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

	// A value set from outside (chat, a reloaded config) decides the view and the level.
	$effect(() => {
		if (isFocusUnits(value)) {
			const next = value.level;
			untrack(() => {
				view = 'place';
				if (next !== 'adm0' && next !== level) {
					level = next;
					zones = null;
					zonesFailed = false;
				}
			});
		} else if (value) view = 'box';
	});

	$effect(() => {
		const whole = countryOfSelection(value);
		if (whole) untrack(() => (lastWhole = whole));
	});

	// The box shows its country's name once the country list is in.
	$effect(() => {
		const name = chosenCountry?.name ?? '';
		untrack(() => (countryText = name));
	});

	// The list is only missed by the country search; a country already chosen still shows.
	onMount(() => {
		getCountries()
			.then((listed) => (countries = listed))
			.catch(() => {});
	});

	// The country's border, when the boundary service knows it. A miss just leaves
	// the basemap, so the map stays usable without boundary data.
	$effect(() => {
		const iso = shownIso;
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

	// A level's places load the first time the country is narrowed to it.
	$effect(() => {
		const atLevel = level;
		const iso = chosenIso;
		if (!narrowing || !iso || zones || zonesFailed) return;
		let stale = false;
		getPlaceBoundary(iso, atLevel)
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

	// The country outline shows what is chosen, or what the data is about while nothing is.
	$effect(() => {
		if (!mapReady) return;
		setData(COUNTRY, view === 'place' ? (countryShape ?? EMPTY) : EMPTY);
		untrack(frame);
	});

	$effect(() => {
		if (!mapReady || !map) return;
		setData(ZONES, zones ?? EMPTY);
		const visibility = narrowing ? 'visible' : 'none';
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
		{#if view === 'place'}
			<div class="country-field">
				<input
					class="country"
					type="search"
					list={COUNTRY_LIST_ID}
					aria-label="Country"
					placeholder="Type a country"
					value={countryText}
					oninput={countryInput}
					onchange={(event) => countryEntered(event.currentTarget.value)}
				/>
				<datalist id={COUNTRY_LIST_ID}>
					{#each countries as listed (listed.iso)}
						<option value={listed.name}></option>
					{/each}
				</datalist>
				{#if chosenIso}
					<button type="button" class="quiet" onclick={clearCountry}>Clear</button>
				{:else if suggested}
					<button type="button" class="suggest" onclick={() => chooseCountry(suggested)}>
						Use {suggested.name}
					</button>
				{/if}
			</div>
		{:else}
			<span class="box-title">Draw a box on the map</span>
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

	{#if view === 'place' && chosenIso}
		<div class="levels" role="group" aria-label="Narrow down">
			{#each LEVELS as option (option.id)}
				<button
					type="button"
					class:active={level === option.id}
					aria-pressed={level === option.id}
					onclick={() => showLevel(option.id)}
				>
					{option.label}
				</button>
			{/each}
		</div>
	{/if}

	<div class="body">
		{#if narrowing}
			<div class="places">
				<input
					type="search"
					class="place-filter"
					aria-label="Find a place"
					placeholder="Find a place"
					bind:value={placeQuery}
				/>
				{#if zonesFailed}
					<p class="hint">
						Couldn't load places.
						<button type="button" class="quiet" onclick={() => (zonesFailed = false)}>Retry</button>
					</p>
				{:else if !zones}
					<p class="hint">Loading places…</p>
				{:else}
					<ul class="place-list" aria-label="Places">
						{#each shownPlaces as name (name)}
							<li>
								<label>
									<input
										type="checkbox"
										checked={picked.includes(name)}
										onchange={() => togglePlace(name)}
									/>
									{name}
								</label>
							</li>
						{:else}
							<li class="hint">No place matches.</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
		<div class="map-frame">
			<div class="map" bind:this={container}></div>
			{#if drawing}
				<p class="map-hint">Click and drag to draw the box</p>
			{/if}
		</div>
	</div>

	<div class="controls">
		{#if view === 'box'}
			<button
				type="button"
				class="draw"
				class:cancel={drawing}
				title={box ? 'Replace the current box with a new one' : 'Limit scoring to a box you draw'}
				onclick={drawing ? stopDrawing : startDrawing}
			>
				{drawing ? 'Cancel' : box ? 'Redraw box' : 'Draw box'}
			</button>
		{/if}
		<small class="summary">
			{#if narrowing && picked.length === 0}
				All of {studyAreaWords(value)}; pick places to narrow it down
			{:else}
				{studyAreaWords(value, unsetLabel)}
			{/if}
		</small>
		{#if box}
			<button type="button" class="quiet" onclick={() => onchange(null)}>Clear</button>
		{/if}
		<button
			type="button"
			class="quiet switch"
			onclick={() => showView(view === 'place' ? 'box' : 'place')}
		>
			{view === 'place' ? 'Not a country or region? Draw a box' : 'Pick a country instead'}
		</button>
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

	.toolbar,
	.country-field,
	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}

	.toolbar {
		justify-content: space-between;
	}

	.country-field {
		flex: 1 1 16rem;
		min-inline-size: 0;
	}

	.country,
	.place-filter {
		flex: 1 1 12rem;
		min-inline-size: 0;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		padding: 0.4rem 0.6rem;
		background: var(--color-bg);
		color: var(--color-text);
		font: inherit;
		font-size: 0.9rem;
	}

	.box-title {
		font-size: 0.9rem;
		font-weight: 600;
	}

	.expand,
	.suggest {
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

	.levels {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
		padding: 0.2rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		background: var(--color-bg);
		inline-size: fit-content;
	}

	.levels button {
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

	.levels button.active {
		background: var(--color-surface-raised);
		color: var(--color-text);
		box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
	}

	.body {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.expanded .body {
		flex: 1;
		min-block-size: 0;
	}

	.place-filter {
		flex: none;
	}

	.places {
		display: flex;
		flex: 1 1 12rem;
		flex-direction: column;
		gap: 0.4rem;
		min-inline-size: 0;
	}

	.place-list {
		display: flex;
		flex-direction: column;
		max-block-size: 16rem;
		margin: 0;
		padding: 0;
		overflow-y: auto;
		list-style: none;
		font-size: 0.9rem;
	}

	.expanded .place-list {
		max-block-size: none;
	}

	.place-list label {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.15rem 0.2rem;
		cursor: pointer;
	}

	.map-frame {
		position: relative;
		flex: 2 1 20rem;
		min-inline-size: 0;
	}

	.expanded .map-frame {
		display: flex;
	}

	.map {
		inline-size: 100%;
		aspect-ratio: 16 / 9;
		min-block-size: 12rem;
		border-radius: 0.375rem;
		overflow: hidden;
	}

	.expanded .map {
		aspect-ratio: auto;
		block-size: 100%;
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

	.hint {
		margin: 0;
		color: var(--color-text-muted);
		font-size: 0.85rem;
	}

	.controls {
		min-block-size: 1.8rem;
	}

	.summary {
		color: var(--color-text-muted);
	}

	.switch {
		margin-inline-start: auto;
	}

	.quiet {
		padding: 0;
		border: 0;
		background: none;
		color: var(--color-accent);
		font: inherit;
		font-size: 0.85rem;
		cursor: pointer;
	}

	.draw {
		border: 0;
		border-radius: 0.4rem;
		padding: 0.3rem 0.7rem;
		background: var(--color-accent);
		color: white;
		font: inherit;
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}

	.draw:hover {
		background: var(--color-accent-hover);
	}

	.draw.cancel {
		border: 1px solid var(--color-border);
		background: var(--color-bg);
		color: var(--color-text);
	}
</style>
