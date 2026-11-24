import { describe, expect, it } from 'vitest';
import { workspaceToOpen } from '../src/lib/workspace/last-opened';

const listed = [{ id: 'newest' }, { id: 'older' }];

describe('workspaceToOpen', () => {
	it('goes back to the workspace last open here', () => {
		expect(workspaceToOpen('older', listed)).toBe('older');
	});

	it('falls back to the most recently updated workspace when none was open here', () => {
		expect(workspaceToOpen(null, listed)).toBe('newest');
	});

	it('passes over a remembered workspace the user no longer has', () => {
		expect(workspaceToOpen('gone', listed)).toBe('newest');
	});

	it('has nothing to open for a user with no workspaces', () => {
		expect(workspaceToOpen('gone', [])).toBeNull();
	});
});
