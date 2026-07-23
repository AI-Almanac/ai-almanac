// Client-side error capture for the feedback breadcrumb trail. Errors are
// recorded, never swallowed: SvelteKit still shows its error page and the
// console still logs.

import type { HandleClientError } from '@sveltejs/kit';
import { addBreadcrumb } from '$lib/breadcrumbs';

// Uncaught errors and unhandled rejections outside SvelteKit's load/navigation
// lifecycle (event handlers, timers, fire-and-forget promises).
window.addEventListener('error', (event) => {
	addBreadcrumb('error', `Uncaught: ${event.message}`, {
		source: `${event.filename ?? ''}:${event.lineno ?? ''}`,
		stack: event.error instanceof Error ? event.error.stack : undefined
	});
});

window.addEventListener('unhandledrejection', (event) => {
	const reason = event.reason;
	addBreadcrumb(
		'error',
		`Unhandled rejection: ${reason instanceof Error ? reason.message : String(reason)}`,
		{
			stack: reason instanceof Error ? reason.stack : undefined
		}
	);
});

export const handleError: HandleClientError = ({ error, event, message }) => {
	addBreadcrumb('error', `Navigation error: ${message}`, {
		route: event.route?.id ?? event.url.pathname,
		stack: error instanceof Error ? error.stack : String(error)
	});
	// Fall through to SvelteKit's default: the shape returned here is what the
	// nearest +error page receives.
	return { message };
};
