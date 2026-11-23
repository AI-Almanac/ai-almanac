/** What a new workspace is made with: the area and season chosen for it, and nothing else. */
import { isFocusUnits, type FocusAreaValue } from '$lib/api/jobs';
import type { SeasonWindow, WorkspaceSettings } from '$lib/api/workspace-settings';

/** The country an area is in, by its three-letter code, when its places name one. */
export function countryOfArea(area: FocusAreaValue | null): string | null {
	return isFocusUnits(area) ? (area.iso ?? null) : null;
}

/**
 * The settings to make a workspace with. A choice left open is left out, so the
 * workspace follows the platform's default for it; nothing chosen is no settings.
 */
export function chosenSettings(
	area: FocusAreaValue | null,
	season: SeasonWindow | null
): WorkspaceSettings | undefined {
	if (!area && !season) return undefined;
	return { ...(area && { study_area: area }), ...(season && { season }) };
}
