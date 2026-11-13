import type { Country } from '$lib/api/places';
import type { Region } from '$lib/api/regions';
import type { FocusUnitLevel, FocusUnits } from '$lib/api/jobs';

/** The country a run's region is, when the region is one. */
export function regionCountry(regions: Region[], regionId: string | null): string | null {
	return regions.find((region) => region.id === regionId)?.boundary_iso ?? null;
}

/** What a region is called, falling back to its id once it is no longer configured; null for none. */
export function regionName(regions: Region[], regionId: string | null | undefined): string | null {
	if (!regionId) return null;
	return regions.find((region) => region.id === regionId)?.display_name ?? regionId;
}

/** A whole country as a place: one unit, named for the country. */
export function wholeCountry(country: Country): FocusUnits {
	return { level: 'adm0', units: [country.name], iso: country.iso };
}

/**
 * The places picked inside a country, or null when none are left. Places name their
 * country so the run is scored there whatever region its data was filed under.
 */
export function pickedPlaces(
	level: FocusUnitLevel,
	units: string[],
	iso: string | null
): FocusUnits | null {
	if (units.length === 0) return null;
	return iso ? { level, units, iso } : { level, units };
}

/** The picked names with one more added, or removed if it was already picked. */
export function toggled(units: string[], name: string): string[] {
	return units.includes(name) ? units.filter((unit) => unit !== name) : [...units, name];
}
