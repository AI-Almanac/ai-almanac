/**
 * Poll `tick` every `intervalMs` for as long as `active()` returns true.
 *
 * Returns a stop function suitable for returning directly from a Svelte
 * `$effect` (Svelte calls it on teardown). The interval is created once, and
 * `active`/`tick` read live state through their closures — so set this up in an
 * `$effect` whose body has no reactive reads, otherwise the effect re-runs and
 * restarts the timer.
 */
export function pollWhileActive(
	active: () => boolean,
	tick: () => void | Promise<void>,
	intervalMs = 3000
): () => void {
	const timer = setInterval(() => {
		if (active()) void tick();
	}, intervalMs);
	return () => clearInterval(timer);
}
