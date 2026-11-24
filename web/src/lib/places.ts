import type { Country } from '$lib/api/places';
import type { Region } from '$lib/api/regions';
import {
	isFocusUnits,
	type FocusAreaValue,
	type FocusUnitLevel,
	type FocusUnits
} from '$lib/api/jobs';

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

/** The places picked at `level`; none when the selection is a whole country, a box, or another level. */
export function pickedAt(area: FocusAreaValue | null, level: FocusUnitLevel): string[] {
	return isFocusUnits(area) && area.level === level && level !== 'adm0' ? area.units : [];
}

/**
 * The selection once a place at `level` is added or removed. Removing the last one
 * goes back to the whole country rather than to nothing, so narrowing down never
 * loses the country.
 */
export function afterToggling(
	area: FocusAreaValue | null,
	level: FocusUnitLevel,
	name: string,
	country: Country | null
): FocusUnits | null {
	const units = toggled(pickedAt(area, level), name);
	if (units.length > 0) return pickedPlaces(level, units, country?.iso ?? null);
	return country ? wholeCountry(country) : null;
}

/** The country a typed name means, ignoring case and spaces around it. */
export function countryNamed(countries: Country[], typed: string): Country | null {
	const wanted = typed.trim().toLowerCase();
	return countries.find((country) => country.name.toLowerCase() === wanted) ?? null;
}

/** The names that contain every word typed, ignoring case, in alphabetical order. */
export function matchingNames(names: string[], query: string): string[] {
	const words = query.toLowerCase().split(/\s+/).filter(Boolean);
	return names
		.filter((name) => words.every((word) => name.toLowerCase().includes(word)))
		.sort((a, b) => a.localeCompare(b));
}

/** The country a whole-country selection names, for when the country list is not to hand. */
export function countryOfSelection(area: FocusAreaValue | null): Country | null {
	if (!isFocusUnits(area) || area.level !== 'adm0' || !area.iso) return null;
	return { iso: area.iso, name: area.units[0] };
}
