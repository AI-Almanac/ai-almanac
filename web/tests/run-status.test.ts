import { describe, expect, it } from 'vitest';

import {
	badgeLabel,
	badgeTone,
	isActiveStatus,
	sidebarStatus,
	statusName
} from '../src/lib/run-status';

describe('where a run stands', () => {
	it('is still going until it completes, fails or is canceled', () => {
		expect(['queued', 'starting', 'running', 'canceling'].every(isActiveStatus)).toBe(true);
		expect(['complete', 'failed', 'canceled'].some(isActiveStatus)).toBe(false);
	});

	it('reads as running on its badge while it waits to start, but says when it is canceling', () => {
		expect(badgeLabel('queued')).toBe('Running');
		expect(badgeLabel('canceling')).toBe('Canceling');
		expect(badgeLabel('complete')).toBe('Complete');
		expect(statusName('queued')).toBe('Queued');
	});

	it('takes a badge colour only when there is something to signal', () => {
		expect(badgeTone('starting')).toBe('running');
		expect(badgeTone('failed')).toBe('failed');
		expect(badgeTone('canceled')).toBe('');
	});

	it('lists an unknown status as mixed and capitalises its name', () => {
		expect(sidebarStatus('canceled')).toBe('canceled');
		expect(sidebarStatus('queued')).toBe('running');
		expect(sidebarStatus('paused')).toBe('mixed');
		expect(statusName('paused')).toBe('Paused');
	});
});
