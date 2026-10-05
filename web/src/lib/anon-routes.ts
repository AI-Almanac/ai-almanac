// Pages anonymous visitors may view: the example results and static pages.
// Everything else keeps the full-page sign-in prompt (see +layout.svelte).
const ANON_ROUTES = new Set([
	'/',
	'/almanac',
	'/attribution',
	'/privacy',
	'/benchmarks',
	'/blends',
	'/forecasts'
]);

export function isAnonRoute(pathname: string): boolean {
	return ANON_ROUTES.has(pathname.replace(/\/+$/, '') || '/');
}
