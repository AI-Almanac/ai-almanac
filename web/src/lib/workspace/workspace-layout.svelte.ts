/**
 * How the workspace's zones are laid out on this screen: which sides are open,
 * how wide each was dragged, and whether the assistant is a column or a sheet.
 * Each side's open state is remembered on this device.
 */
import type { ProductKind } from './products';
import {
	assistantForm,
	assistantShown,
	isUnreadReply,
	remembersAssistant,
	restoreZones,
	storedOpen,
	zoneWidths,
	type AssistantForm,
	type Zones
} from './zones';

const PRODUCTS_ZONE_KEY = 'almanac.workspace.zone.products';
const ASSISTANT_ZONE_KEY = 'almanac.workspace.zone.assistant';

export class WorkspaceLayout {
	zones = $state<Zones>({ productsOpen: true, assistantOpen: true });
	/** The kind the products list was expanded at, which it scrolls to. */
	productsOpenAt = $state<ProductKind | null>(null);
	/** The widths dragged for each side, in pixels; null leaves a side at its default. */
	productsWidth = $state<number | null>(null);
	assistantWidth = $state<number | null>(null);
	form = $state<AssistantForm>('column');
	sheetOpen = $state(false);
	/** Two answers are side by side, which needs a wider assistant. */
	comparing = $state(false);
	/** A reply landed while the assistant was out of sight. */
	unreadReply = $state(false);
	/** Where the page starts, below the navigation and any banner above it. */
	pageTop = $state(0);

	readonly assistantOpen = $derived(assistantShown(this.form, this.zones, this.sheetOpen));
	readonly widths = $derived(
		zoneWidths(
			this.zones,
			{ products: this.productsWidth, assistant: this.assistantWidth },
			this.comparing
		)
	);

	/** Each side as it was left on this device, else the default for this screen. */
	restore(storage: Storage, viewportWidth: number) {
		this.zones = restoreZones(
			{
				products: storage.getItem(PRODUCTS_ZONE_KEY),
				assistant: storage.getItem(ASSISTANT_ZONE_KEY)
			},
			viewportWidth
		);
	}

	setProductsOpen(open: boolean, at: ProductKind | null = null) {
		this.zones = { ...this.zones, productsOpen: open };
		this.productsOpenAt = at;
		localStorage.setItem(PRODUCTS_ZONE_KEY, storedOpen(open));
	}

	setAssistantOpen(open: boolean) {
		if (open) this.unreadReply = false;
		if (!remembersAssistant(this.form)) {
			this.sheetOpen = open;
			return;
		}
		this.zones = { ...this.zones, assistantOpen: open };
		localStorage.setItem(ASSISTANT_ZONE_KEY, storedOpen(open));
	}

	/** The conversation went from `before` messages to `after`. */
	transcriptGrew(before: number, after: number) {
		if (isUnreadReply(before, after, this.assistantOpen)) this.unreadReply = true;
	}

	/**
	 * Follows where the page starts and how wide the window is. The nav wraps with
	 * the window and a banner can sit above it, so both are only known at runtime;
	 * the body resizes whenever either moves the page. Returns what stops it.
	 */
	track(pageEl: HTMLElement): () => void {
		const measure = () => {
			this.pageTop = pageEl.getBoundingClientRect().top + window.scrollY;
			this.form = assistantForm(window.innerWidth);
		};
		measure();
		const moved = new ResizeObserver(measure);
		moved.observe(document.body);
		window.addEventListener('resize', measure);
		return () => {
			moved.disconnect();
			window.removeEventListener('resize', measure);
		};
	}
}
