import { describe, expect, it } from 'vitest';

import { sectionSlug, settingsNav } from '$lib/settings/sections';
import type { SettingsGroup } from '$lib/api';

const GROUPS = [
	{ name: 'Benchmark runner', fields: [] },
	{ name: 'Assistant guardrails', fields: [] }
] as unknown as SettingsGroup[];

function labels(nav: ReturnType<typeof settingsNav>): string[] {
	return nav.flatMap((group) => group.links.map((link) => link.label));
}

describe('settings navigation', () => {
	it('files assistant config groups under Assistant and the rest under Platform', () => {
		const [platform, assistant] = settingsNav(GROUPS);

		expect(platform.links.map((l) => l.label)).toContain('Benchmark runner');
		expect(assistant.links.map((l) => l.label)).toContain('Assistant guardrails');
	});

	it('drops the comparison page when the feature is off', () => {
		expect(labels(settingsNav(GROUPS))).toContain('Compare rulesets');
		// The endpoints 404 with the flag off, so a link to them would be a dead end.
		expect(labels(settingsNav(GROUPS, { comparisonsEnabled: false }))).not.toContain(
			'Compare rulesets'
		);
		// Rulesets and Feedback are not comparison features and stay.
		expect(labels(settingsNav(GROUPS, { comparisonsEnabled: false }))).toEqual(
			expect.arrayContaining(['Rulesets', 'Feedback', 'Model & API keys'])
		);
	});

	it('slugs a group name into its route', () => {
		expect(sectionSlug('Weather data access')).toBe('weather-data-access');
		expect(sectionSlug('AI assistant')).toBe('ai-assistant');
	});
});
