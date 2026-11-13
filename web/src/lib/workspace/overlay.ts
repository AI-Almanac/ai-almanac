/**
 * Keyboard manners for something that opens over the page (a panel over the
 * stage, a popover): focus moves into it, Escape closes it, and focus goes back
 * to whatever opened it.
 */
export type OverlayOptions = {
	onClose: () => void;
	/** What opened it; whatever had focus when it opened, unless said. */
	opener?: HTMLElement | null;
	/** A click anywhere else closes it too. */
	closeOnOutsideClick?: boolean;
};

function focusedElement(): HTMLElement | null {
	return document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

/** Focus went nowhere in particular, so handing it back takes it from nothing. */
function focusIsAdrift(node: HTMLElement): boolean {
	const focused = document.activeElement;
	return !focused || focused === document.body || node.contains(focused);
}

export function overlay(node: HTMLElement, options: OverlayOptions) {
	const opener = options.opener ?? focusedElement();
	if (!node.hasAttribute('tabindex')) node.tabIndex = -1;
	node.focus();

	function closeOnEscape(event: KeyboardEvent) {
		if (event.key !== 'Escape' || event.defaultPrevented) return;
		event.preventDefault();
		event.stopPropagation();
		options.onClose();
	}

	function closeFromOutside(event: MouseEvent) {
		const target = event.target as Node;
		if (!node.contains(target) && !opener?.contains(target)) options.onClose();
	}

	node.addEventListener('keydown', closeOnEscape);
	if (options.closeOnOutsideClick) document.addEventListener('click', closeFromOutside, true);

	return {
		destroy() {
			node.removeEventListener('keydown', closeOnEscape);
			document.removeEventListener('click', closeFromOutside, true);
			if (opener?.isConnected && focusIsAdrift(node)) opener.focus();
		}
	};
}
