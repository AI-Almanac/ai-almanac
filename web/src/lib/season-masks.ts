// Regions whose benchmarks and blends score only their season's rain areas
// unless switched off (season_mask: false). Mirrors SEASON_MASKS in
// server/services/focus_area.py and envs/blend_recipe.py.
const SEASON_MASKS: Record<string, string> = {
	ethiopia: 'June–September rain areas only'
};

/** The season-mask toggle's label for a region, or null when it has no mask. */
export function seasonMaskLabel(region: string | null | undefined): string | null {
	return SEASON_MASKS[(region ?? '').trim().toLowerCase()] ?? null;
}
