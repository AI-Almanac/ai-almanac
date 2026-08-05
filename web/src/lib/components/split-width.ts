/**
 * The assistant column's width, as a number of pixels the page will honour.
 *
 * Separate from the component so the clamping — the part with an off-by-one
 * that a screenshot would not catch — is testable without a DOM.
 */

/** Narrower than this and the assistant is unusable for prose. */
export const MIN_ASIDE_PX = 352; // 22rem
/** Past this the results stop being the dominant pane, which is the point of
 * the layout; a comparison gets its own floor instead (see COMPARE_MIN_PX). */
export const MAX_ASIDE_FRACTION = 0.6;
/** Two answers side by side need at least this much, whatever width was saved. */
export const COMPARE_MIN_PX = 640; // 40rem

export function clampAside(width: number, containerWidth: number): number {
	const max = Math.max(MIN_ASIDE_PX, Math.round(containerWidth * MAX_ASIDE_FRACTION));
	return Math.min(Math.max(Math.round(width), MIN_ASIDE_PX), max);
}

/**
 * The width to render: the user's choice, except that an open comparison keeps
 * a floor so it cannot be squeezed into ribbons by an earlier narrow drag.
 */
export function effectiveAside(width: number, containerWidth: number, comparing: boolean): number {
	const wanted = comparing ? Math.max(width, COMPARE_MIN_PX) : width;
	return clampAside(wanted, containerWidth);
}
