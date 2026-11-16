/**
 * The workspace's three zones: the products list, the stage, and the
 * assistant. The list and the assistant each collapse to a strip, on their own.
 */
import { COMPARE_MIN_PX, type PaneLimits } from '$lib/components/split-width';

export type Zones = { productsOpen: boolean; assistantOpen: boolean };

/** Narrower than this, an open list leaves the stage too little room to start with. */
const PRODUCTS_OPEN_MIN_WIDTH = 1280;

const PRODUCTS_WIDTH = '14rem';
const ASSISTANT_WIDTH = '28rem';
const STRIP_WIDTH = '3rem';

const OPEN = 'open';
const COLLAPSED = 'collapsed';

/** What was remembered for one side, or null when nothing usable was. */
function parseOpen(saved: string | null): boolean | null {
	if (saved === OPEN) return true;
	if (saved === COLLAPSED) return false;
	return null;
}

export function storedOpen(open: boolean): string {
	return open ? OPEN : COLLAPSED;
}

export function defaultZones(viewportWidth: number): Zones {
	return { productsOpen: viewportWidth >= PRODUCTS_OPEN_MIN_WIDTH, assistantOpen: true };
}

/** Each side as it was left, falling back to the default for this screen. */
export function restoreZones(
	saved: { products: string | null; assistant: string | null },
	viewportWidth: number
): Zones {
	const fallback = defaultZones(viewportWidth);
	return {
		productsOpen: parseOpen(saved.products) ?? fallback.productsOpen,
		assistantOpen: parseOpen(saved.assistant) ?? fallback.assistantOpen
	};
}

/**
 * At this width and below the zones stack and the assistant is a sheet over the
 * bottom of the screen. The stylesheets' `max-width: 1050px` queries match it.
 */
const SHEET_MAX_WIDTH = 1050;

/** A column beside the stage, or a sheet that opens over the bottom of the screen. */
export type AssistantForm = 'column' | 'sheet';

export function assistantForm(viewportWidth: number): AssistantForm {
	return viewportWidth <= SHEET_MAX_WIDTH ? 'sheet' : 'column';
}

/**
 * Whether the conversation is showing. The column is as it was left; the sheet
 * covers most of the screen, so it is only ever open because it was just asked for.
 */
export function assistantShown(form: AssistantForm, zones: Zones, sheetOpen: boolean): boolean {
	return form === 'sheet' ? sheetOpen : zones.assistantOpen;
}

/** The sheet is opened for the moment, so only the column's state is worth remembering. */
export function remembersAssistant(form: AssistantForm): boolean {
	return form === 'column';
}

/** Wide enough to read the assistant's prose; never more than half the shell. */
const ASSISTANT_LIMITS: PaneLimits = { min: 320, maxFraction: 0.5 };

export function assistantLimits(comparing: boolean): PaneLimits {
	return comparing ? { min: COMPARE_MIN_PX, maxFraction: 0.6 } : ASSISTANT_LIMITS;
}

/** The open assistant: the chosen pixels, held to the comparison floor while comparing. */
function openAssistantWidth(chosen: number | null, comparing: boolean): string {
	if (chosen) return `${comparing ? Math.max(chosen, COMPARE_MIN_PX) : chosen}px`;
	return comparing ? '40rem' : ASSISTANT_WIDTH;
}

export type ZoneWidths = { products: string; assistant: string };

/** Wide enough for a run's name and status; never the dominant column. */
export const PRODUCTS_LIMITS: PaneLimits = { min: 176, maxFraction: 0.35 };

/** The width dragged for each side, in pixels; null leaves that side at its default. */
export type ChosenWidths = { products: number | null; assistant: number | null };

/** The CSS width of each side; the stage takes whatever they leave. */
export function zoneWidths(zones: Zones, chosen: ChosenWidths, comparing: boolean): ZoneWidths {
	const products = chosen.products ? `${chosen.products}px` : PRODUCTS_WIDTH;
	return {
		products: zones.productsOpen ? products : STRIP_WIDTH,
		assistant: zones.assistantOpen ? openAssistantWidth(chosen.assistant, comparing) : STRIP_WIDTH
	};
}

/** What the collapsed assistant's button says: what pressing it does, and what is waiting. */
export function assistantStripLabel(working: boolean, unread: boolean): string {
	if (working) return 'Open the assistant (working)';
	if (unread) return 'Open the assistant (new reply)';
	return 'Open the assistant';
}

/** A reply that lands while the assistant is collapsed is marked, not forced into view. */
export function isUnreadReply(before: number, after: number, assistantOpen: boolean): boolean {
	return !assistantOpen && before > 0 && after > before;
}
