import { beforeEach, describe, expect, it } from 'vitest';

import { hasSeenTour, queueTour, runTour, takeQueuedTour } from '../src/lib/tour.svelte';

const step = [{ popover: { title: 'Hi', description: 'There' } }];

describe('tour queueing', () => {
	beforeEach(() => localStorage.clear());

	it('marks a tour seen once it has run', () => {
		expect(hasSeenTour('landing')).toBe(false);
		runTour('landing', step).destroy();
		expect(hasSeenTour('landing')).toBe(true);
	});

	it('hands a queued tour out exactly once', () => {
		queueTour('benchmark-results');
		expect(takeQueuedTour('benchmark-results')).toBe(true);
		expect(takeQueuedTour('benchmark-results')).toBe(false);
	});

	it('does not let a queued tour start a different one', () => {
		queueTour('landing');
		expect(takeQueuedTour('benchmark-setup')).toBe(false);
	});
});
describe('tour progress', () => {
	beforeEach(() => localStorage.clear());
	const steps = [1, 2, 3].map((n) => ({ popover: { title: `Step ${n}`, description: '…' } }));

	it('resumes where the user closed it', () => {
		const tour = runTour('landing', steps, { animate: false });
		tour.moveNext();
		tour.destroy();
		const resumed = runTour('landing', steps, { animate: false });
		expect(resumed.getActiveIndex()).toBe(1);
		resumed.destroy();
	});

	it('starts over after the last step', () => {
		const tour = runTour('landing', steps, { animate: false });
		tour.moveTo(2);
		tour.destroy();
		const again = runTour('landing', steps, { animate: false });
		expect(again.getActiveIndex()).toBe(0);
		again.destroy();
	});
});
