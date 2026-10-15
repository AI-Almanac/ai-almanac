import { describe, expect, it } from 'vitest';

import {
	attributionSections,
	forecastModelSources,
	groundTruthSources,
	mapDataSources
} from '$lib/legal/attribution';

const ALL_ENTRIES = [...groundTruthSources, ...forecastModelSources, ...mapDataSources];

describe('attribution catalog', () => {
	it('states a provider, license, and at least one citation and https link for every entry', () => {
		for (const entry of ALL_ENTRIES) {
			expect(entry.provider, entry.slug).not.toBe('');
			expect(entry.license, entry.slug).not.toBe('');
			expect(entry.usage, entry.slug).not.toBe('');
			expect(entry.citations.length, entry.slug).toBeGreaterThan(0);
			expect(entry.links.length, entry.slug).toBeGreaterThan(0);
			for (const link of entry.links) {
				expect(link.href, link.label).toMatch(/^https:\/\//);
				expect(link.label, link.href).not.toBe('');
			}
		}
	});

	it('keeps slugs unique so the in-page anchors resolve', () => {
		const slugs = ALL_ENTRIES.map((entry) => entry.slug);
		expect(new Set(slugs).size).toBe(slugs.length);
	});

	it('lists every entry in exactly one section', () => {
		const listed = attributionSections.flatMap((section) => section.entries);
		for (const entry of ALL_ENTRIES) {
			expect(
				listed.filter((candidate) => candidate === entry),
				entry.slug
			).toHaveLength(1);
		}
		expect(listed).toHaveLength(ALL_ENTRIES.length);
	});
});
