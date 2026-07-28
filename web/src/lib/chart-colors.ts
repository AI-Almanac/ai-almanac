/**
 * Shared series colors for uPlot charts.
 *
 * MaeSeriesChart and BlendSkillChart each carried their own MODEL_COLORS array
 * with divergent contents, so the same model rendered in different colors
 * depending on which chart you were looking at. Both now import from here.
 *
 * uPlot needs concrete color strings at construction time, so these cannot be
 * CSS custom properties.
 */

/** Assigned to model series in order, cycling if there are more models. */
export const MODEL_COLORS = [
	'#0f766e',
	'#2166ac',
	'#b2182b',
	'#6b5b95',
	'#d06f1a',
	'#2d7d46',
	'#9333ea',
	'#0891b2'
] as const;

/** Climatology / reference series — drawn dashed to read as a baseline. */
export const BASELINE_COLOR = '#8a6f3d';

/** The blended model, drawn heavier than its constituents. */
export const BLEND_COLOR = '#1f2937';

/** Axis strokes, gridlines, and other chart chrome. */
export const AXIS_STROKE = '#6a7779';
export const GRID_STROKE = 'rgba(31, 43, 52, 0.1)';

/** No-skill / parity reference lines (BSS = 0, AUC = 0.5). */
export const REFERENCE_STROKE = 'rgba(31, 43, 52, 0.35)';

/** Color for the nth model series, cycling through MODEL_COLORS. */
export function modelColor(index: number): string {
	return MODEL_COLORS[index % MODEL_COLORS.length];
}
