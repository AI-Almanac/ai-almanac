import { describe, expect, it } from 'vitest';
import { matchingWorkspaces } from '../src/lib/workspace/workspace-switcher';

const workspaces = [
	{ name: 'India 2026', description: 'Monsoon onset 2026 season' },
	{ name: 'Ethiopia 2026', description: 'Kiremt onset 2026 season' },
	{ name: 'Test', description: '' }
];

describe('matchingWorkspaces', () => {
	it('lists every workspace until something is typed', () => {
		expect(matchingWorkspaces(workspaces, '  ')).toEqual(workspaces);
	});

	it('finds a workspace by a word in its description, ignoring case', () => {
		expect(matchingWorkspaces(workspaces, 'kiremt').map((ws) => ws.name)).toEqual([
			'Ethiopia 2026'
		]);
	});

	it('keeps only workspaces that mention every word typed', () => {
		expect(matchingWorkspaces(workspaces, '2026 monsoon').map((ws) => ws.name)).toEqual([
			'India 2026'
		]);
	});
});
