import { describe, expect, it } from 'vitest';
import { figureCard } from '../src/lib/chat/reply-cards';

describe('reply cards', () => {
	it('names a figure as the workspace calls it now', () => {
		const card = figureCard(
			{ label: 'onset_map', workspace_artifact_id: 'art-1' },
			{ 'art-1': 'Onset map, Amhara' }
		);

		expect(card).toEqual({ name: 'Onset map, Amhara', kind: 'Figure' });
	});

	it('falls back to the assistant’s label, then to a plain word', () => {
		expect(figureCard({ label: 'Onset map' }).name).toBe('Onset map');
		expect(figureCard({ label: null }).name).toBe('Figure');
	});
});
