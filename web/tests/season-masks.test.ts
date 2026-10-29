import { describe, expect, it } from 'vitest';

import { seasonMaskLabel } from '../src/lib/season-masks';

describe('season masks', () => {
	it('names the toggle only for regions with a season mask', () => {
		expect(seasonMaskLabel('ethiopia')).toBe('June–September rain areas only');
		expect(seasonMaskLabel(' Ethiopia ')).toBe('June–September rain areas only');
		expect(seasonMaskLabel('india')).toBeNull();
		expect(seasonMaskLabel(null)).toBeNull();
	});
});
