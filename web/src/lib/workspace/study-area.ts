import { isFocusUnits, type FocusAreaValue } from '$lib/api/jobs';

/** The place a workspace works in, in a few words: its places by name, or its box. */
export function studyAreaWords(area: FocusAreaValue | null | undefined): string {
	if (!area) return 'All of the data';
	if (isFocusUnits(area)) return area.units.join(', ');
	return `${area.lat_min}° to ${area.lat_max}° lat, ${area.lon_min}° to ${area.lon_max}° lon`;
}

/** A new run's starting point when all the workspace gives it is where to score. */
export function draftIn(
	area: FocusAreaValue | null | undefined
): { model_ids: string[]; focus_area: FocusAreaValue } | undefined {
	return area ? { model_ids: [], focus_area: area } : undefined;
}
