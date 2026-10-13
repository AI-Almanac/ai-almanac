import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import CellInspector from '../src/lib/components/CellInspector.svelte';
import { WEEKLY_BINS } from '../src/lib/onset';

// Two forecasts place onset on Jun 2; the three after it ring Week 1, the way a
// blend does once the rains are under way. Those three count as issued after onset.
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
	it.each([
		{ case: 'notes', date: '2025-07-10', noticeShown: true },
		{ case: 'does not note', date: '2025-05-15', noticeShown: false }
	])(
		'$case that a forecast issued after onset does not describe a new onset',
		({ date, noticeShown }) => {
			renderInspector(date);
			const summary = screen.getByRole('region', { name: 'Selected forecast' });
			expect(within(summary).queryByText(/new onset/i) !== null).toBe(noticeShown);
		}
	);

	it('folds forecasts issued after onset until asked to show them', async () => {
		renderInspector('2025-05-15');
		expect(
			screen.getByText(/3 forecasts issued after onset likely began \(Jun 12 – Jul 10\)/)
		).toBeTruthy();
		expect(screen.queryByText('Jun 12')).toBeNull();

		await fireEvent.click(screen.getByRole('button', { name: 'Show' }));

		expect(screen.getByText('Jun 12')).toBeTruthy();
		expect(screen.getByText('Jul 10')).toBeTruthy();
	});
});
