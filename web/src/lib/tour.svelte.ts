import { onDestroy, tick, untrack } from 'svelte';
import { driver, type Config, type Driver, type DriveStep } from 'driver.js';
import { hints, type Hints, type PopoverDOM } from 'driver.js/hints';
import 'driver.js/dist/driver.css';
import 'driver.js/dist/hints.css';

// ponytail: localStorage, like the assistant beta note — re-showing a tour on a
// new device is the harmless failure.
const SEEN_PREFIX = 'almanac.tourSeen.';
const STEP_PREFIX = 'almanac.tourStep.';

let queuedTour: string | null = null;
let running: Driver | null = null;
let helpHint: Hints | null = null;

/** The tour for whatever is on screen, so the nav help button can restart it. */
export const activeTour = $state<{ restart: (() => void) | null }>({ restart: null });

export function hasSeenTour(id: string): boolean {
	return localStorage.getItem(SEEN_PREFIX + id) === '1';
}

/** A tour that hands off to another view asks for the next one to start on arrival. */
export function queueTour(id: string): void {
	queuedTour = id;
}

/** Consumes the queue: a queued tour starts once, on the first view that installs it. */
export function takeQueuedTour(id: string): boolean {
	const queued = queuedTour === id;
	if (queued) queuedTour = null;
	return queued;
}

/** First visit to a view: a pulsing beacon on the help button, no overlay. */
function showHelpHint(): void {
	hideHelpHint();
	helpHint = hints({
		overlay: false,
		popoverClass: 'almanac-tour',
		hints: [
			{
				id: 'help',
				element: '[data-tour="help"]',
				beacon: { side: 'bottom', align: 'center' },
				popover: {
					title: 'New here?',
					description: 'Click the ? any time for a guided walkthrough of this page.',
					side: 'bottom',
					align: 'center',
					showButton: true,
					buttonText: 'Start walkthrough',
					onButtonClick: () => activeTour.restart?.(),
					onPopoverRender: addCloseButton
				}
			}
		]
	});
	helpHint.show();
	helpHint.open('help');
}

// The hints popover has no close control of its own; reuse driver.css's corner button.
function addCloseButton(popover: PopoverDOM, { hints }: { hints: Hints }): void {
	if (popover.wrapper.querySelector('.driver-popover-close-btn')) return;
	const close = document.createElement('button');
	close.type = 'button';
	close.className = 'driver-popover-close-btn';
	close.setAttribute('aria-label', 'Close');
	close.textContent = '×';
	close.addEventListener('click', () => hints.close());
	popover.wrapper.appendChild(close);
}

function hideHelpHint(): void {
	helpHint?.hide();
	helpHint = null;
}

function savedStep(id: string, stepCount: number): number {
	const step = Number(localStorage.getItem(STEP_PREFIX + id));
	return step > 0 && step < stepCount ? step : 0;
}

/** Closing mid-tour keeps the place; reaching the last step clears it. */
export function runTour(id: string, steps: DriveStep[], config: Config = {}): Driver {
	localStorage.setItem(SEEN_PREFIX + id, '1');
	running?.destroy();
	const tour = driver({
		showProgress: true,
		waitForElement: 3000,
		skipMissingElement: true,
		popoverClass: 'almanac-tour',
		...config,
		steps,
		onHighlightStarted: (element, _step, { state }) => {
			// driver.js skips scrolling for anything inside the viewport, even under the sticky nav.
			element?.scrollIntoView({ block: 'center' });
			const index = state.activeIndex ?? 0;
			// ponytail: reaching the final step counts as finishing the tour.
			if (index >= steps.length - 1) localStorage.removeItem(STEP_PREFIX + id);
			else localStorage.setItem(STEP_PREFIX + id, String(index));
		},
		onDestroyed: () => {
			if (running === tour) running = null;
		}
	});
	running = tour;
	tour.drive(savedStep(id, steps.length));
	return tour;
}

/**
 * Call during component init. While `active` holds, the help button restarts this
 * tour. A queued tour starts itself; otherwise a first visit just points at the button.
 * Unmounting the component ends whatever tour is running.
 */
export function installTour(
	id: string,
	steps: () => DriveStep[],
	active: () => boolean = () => true
): void {
	const start = () => {
		hideHelpHint();
		return runTour(id, untrack(steps));
	};
	$effect(() => {
		if (!active()) return;
		activeTour.restart = start;
		if (takeQueuedTour(id)) untrack(start);
		else if (!hasSeenTour(id)) {
			localStorage.setItem(SEEN_PREFIX + id, '1');
			// The help button renders once restart is set; wait for it before pointing at it.
			void tick().then(() => activeTour.restart === start && showHelpHint());
		}
		return () => {
			if (activeTour.restart === start) {
				activeTour.restart = null;
				hideHelpHint();
			}
		};
	});
	onDestroy(() => running?.destroy());
}
