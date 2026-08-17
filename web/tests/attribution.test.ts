import { describe, expect, it } from 'vitest';

import {
	attributionSections,
	forecastModelSources,
	groundTruthSources
} from '$lib/legal/attribution';

const ALL_ENTRIES = [...groundTruthSources, ...forecastModelSources];

describe('attribution catalog', () => {
	it('credits every ground-truth source and forecast model the platform ships by default', () => {
		expect(groundTruthSources.map((entry) => entry.name)).toEqual([
			'CHIRPS v3 daily (IMERG)',
			'IMERG'
		]);
		expect(forecastModelSources.map((entry) => entry.name)).toEqual([
			'AIFS-single-v1p1',
			'AIFS-ENS-v1',
			'AIFS-single-v2',
			'AIFS-ENS-v2',
			'FuXi-S2S',
			'GenCast',
			'GraphCast',
			'IFS-S2S (TIGGE)',
			'NeuralGCM (IMERG, precip)',
			'FuXi (deterministic)'
		]);
	});

	it('states a provider, license, and at least one citation and link for every entry', () => {
		for (const entry of ALL_ENTRIES) {
			expect(entry.provider, entry.slug).not.toBe('');
			expect(entry.license, entry.slug).not.toBe('');
			expect(entry.usage, entry.slug).not.toBe('');
			expect(entry.citations.length, entry.slug).toBeGreaterThan(0);
			expect(entry.links.length, entry.slug).toBeGreaterThan(0);
		}
	});

	it('keeps slugs unique so the in-page anchors resolve', () => {
		const slugs = ALL_ENTRIES.map((entry) => entry.slug);
		expect(new Set(slugs).size).toBe(slugs.length);
	});

	it('uses absolute https URLs for provider links', () => {
		for (const link of ALL_ENTRIES.flatMap((entry) => entry.links)) {
			expect(link.href, link.label).toMatch(/^https:\/\//);
			expect(link.label, link.href).not.toBe('');
		}
	});

	it('renders both sections in the order the page walks them', () => {
		expect(attributionSections.map((section) => section.slug)).toEqual(['ground-truth', 'models']);
		expect(attributionSections.flatMap((section) => section.entries)).toEqual(ALL_ENTRIES);
	});
});
