import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import CellInspector from '../src/lib/components/CellInspector.svelte';
import { WEEKLY_BINS } from '../src/lib/onset';

// Two forecasts place onset around Jun 2; the three after it ring Week 1, the
// way a blend does once the rains are under way. That drags the estimate to
// about Jun 19, so the Jun 26 and Jul 10 forecasts count as issued after onset.
const ISSUE_DATES = [
	'2025-05-01',
	'2025-05-15',
	'2025-05-29',
	'2025-06-12',
	'2025-06-26',
	'2025-07-10'
];
const PROBS = [
	[0, 0, 0, 0, 1],
	[0, 0, 1, 0, 0],
	[1, 0, 0, 0, 0],
	[1, 0, 0, 0, 0],
	[1, 0, 0, 0, 0],
	[1, 0, 0, 0, 0]
];

function renderInspector(selectedDate: string) {
	return render(CellInspector, {
		point: { lat: 9, lon: 38, probs: PROBS },
		bins: WEEKLY_BINS,
		issueDates: ISSUE_DATES,
		onsetName: 'Rainy season onset',
		selectedDate,
		soonestColor: 'yellow',
		onClose: () => {}
	});
}

describe('CellInspector', () => {
	it('says when onset is more than four weeks out rather than naming a fifth window', () => {
		renderInspector('2025-05-01');
		expect(screen.getByText('more than 4 weeks away (after May 29)')).toBeTruthy();
	});

	it('explains that a forecast issued after onset does not describe a new onset', () => {
		renderInspector('2025-07-10');
		expect(screen.getByText('Jun 16 – Jun 22')).toBeTruthy();
		expect(screen.getByText(/doesn't describe a new onset/)).toBeTruthy();
	});

	it('folds forecasts issued after onset until asked to show them', async () => {
		renderInspector('2025-05-15');
		expect(screen.getByText(/2 forecasts issued after onset likely began/)).toBeTruthy();
		expect(screen.queryByText('Jul 10')).toBeNull();

		await fireEvent.click(screen.getByRole('button', { name: 'Show' }));

		expect(screen.getByText('Jul 10')).toBeTruthy();
	});
});
