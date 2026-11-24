import { isFocusUnits, type FocusAreaValue } from '$lib/api/jobs';

/** Past this many places, the rest are counted rather than named. */
const NAMED_PLACES = 3;

/**
 * An area in a few words: its country or places by name, or its box. `unset` is what
 * no area means where it is shown, which differs between a workspace and a run.
 */
export function studyAreaWords(area: FocusAreaValue | null | undefined, unset = 'Not set'): string {
	if (!area) return unset;
	if (isFocusUnits(area)) return placeWords(area.units);
	return `Box ${degreeRange(area.lat_min, area.lat_max, 'N', 'S')}, ${degreeRange(area.lon_min, area.lon_max, 'E', 'W')}`;
}

function placeWords(units: string[]): string {
	const named = units.slice(0, NAMED_PLACES).join(', ');
	const rest = units.length - NAMED_PLACES;
	return rest > 0 ? `${named} +${rest} more` : named;
}

/** "8–12°N", or "5°S–3°N" when the range crosses the equator or the prime meridian. */
function degreeRange(from: number, to: number, positive: string, negative: string): string {
	const hemisphere = (value: number) => (value < 0 ? negative : positive);
	const magnitude = (value: number) => `${Math.abs(value)}°`;
	if (hemisphere(from) === hemisphere(to)) {
		return `${Math.abs(from)}–${magnitude(to)}${hemisphere(to)}`;
	}
	return `${magnitude(from)}${hemisphere(from)}–${magnitude(to)}${hemisphere(to)}`;
}

/** A new run's starting point when all the workspace gives it is where to score. */
export function draftIn(
	area: FocusAreaValue | null | undefined
): { model_ids: string[]; focus_area: FocusAreaValue } | undefined {
	return area ? { model_ids: [], focus_area: area } : undefined;
}
