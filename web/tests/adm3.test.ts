import { describe, expect, it } from 'vitest';

import {
	buildAdm3ForecastGeoJson,
	normalizeAreaName,
	usesNamedAreas
} from '../src/lib/components/blend-map/adm3';
import type { BlendForecastData } from '../src/lib/api';

describe('ADM3 forecast geometry', () => {
	it('normalizes area names for boundary joins', () => {
		expect(normalizeAreaName("Abichugna Gne'a")).toBe('abichugna gnea');
		expect(normalizeAreaName('  ABICHUGNA   GNE’A  ')).toBe('abichugna gnea');
	});

	it('detects named forecast ids separately from lat/lon ids', () => {
		expect(usesNamedAreas([{ id: '9.2500_38.7500', lat: 9.25, lon: 38.75, probs: [] }])).toBe(
			false
		);
		expect(usesNamedAreas([{ id: 'Ada’a', lat: 8.9, lon: 38.8, probs: [] }])).toBe(true);
	});

	it('builds a polygon forecast layer by joining ADM3 names', () => {
		const data: BlendForecastData = {
			issue_dates: ['2026-05-30'],
			points: [{ id: 'Ada’a', lat: 8.9, lon: 38.8, probs: [[[0.6, 0.2, 0.1, 0.1, 0]]][0] }],
			onset_threshold: 20,
			region_id: 'ethiopia',
			region_name: 'Ethiopia',
			onset_definition: null
		};
		const boundaries: GeoJSON.FeatureCollection = {
			type: 'FeatureCollection',
			features: [
				{
					type: 'Feature',
					properties: { shapeName: 'Ada’a' },
					geometry: {
						type: 'Polygon',
						coordinates: [
							[
								[38.7, 8.8],
								[38.9, 8.8],
								[38.9, 9],
								[38.7, 9],
								[38.7, 8.8]
							]
						]
					}
				},
				{
					type: 'Feature',
					properties: { shapeName: 'Other' },
					geometry: {
						type: 'Polygon',
						coordinates: [
							[
								[39, 9],
								[39.1, 9],
								[39.1, 9.1],
								[39, 9.1],
								[39, 9]
							]
						]
					}
				}
			]
		};

		const geojson = buildAdm3ForecastGeoJson(data, boundaries, () => ({
			color: '#ffee00',
			opacity: 0.8
		}));

		expect(geojson?.features).toHaveLength(1);
		expect(geojson?.features[0].properties).toEqual({ color: '#ffee00', opacity: 0.8, idx: 0 });
	});
});
