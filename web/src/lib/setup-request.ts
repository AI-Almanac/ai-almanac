import { goto } from '$app/navigation';
import { page } from '$app/state';

/**
 * Reads a `?<name>=1` request to open a setup form and drops it from the URL,
 * so a refresh or back-navigation doesn't reopen the form. A real navigation,
 * because shallow `replaceState` keeps the flagged URL in history; deferred,
 * because on a fresh load this runs before SvelteKit's router has started.
 */
export function takeSetupRequest(name: string): boolean {
	if (page.url.searchParams.get(name) !== '1') return false;
	const url = new URL(page.url);
	url.searchParams.delete(name);
	setTimeout(() => goto(url, { replaceState: true, noScroll: true, keepFocus: true }));
	return true;
}
