import { describe, expect, it } from 'vitest';
import type { FeatureCollection } from 'geojson';

import {
	boxBetween,
	boxOutline,
	isDegenerate,
	overlaps,
	parseBounds,
	shapeBounds
} from '../src/lib/geo/bbox';

const ethiopia = { lat_min: 3, lat_max: 15, lon_min: 33, lon_max: 48 };

describe('a box drawn between two corners', () => {
	it('spans them whichever way it was dragged, to the hundredth of a degree', () => {
		expect(boxBetween({ lng: 40.123, lat: 9.987 }, { lng: 35.001, lat: 4.5 })).toEqual({
			lat_min: 4.5,
			lat_max: 9.99,
			lon_min: 35,
			lon_max: 40.12
		});
	});

	it('has no area when both corners share a line', () => {
		expect(isDegenerate(boxBetween({ lng: 40, lat: 9 }, { lng: 40, lat: 12 }))).toBe(true);
		expect(isDegenerate(ethiopia)).toBe(false);
	});

	it('is outlined as one closed polygon, and nothing is outlined for no box', () => {
		const outline = boxOutline(ethiopia);
		expect(outline.features).toHaveLength(1);
		const ring = (outline.features[0].geometry as { coordinates: number[][][] }).coordinates[0];
		expect(ring[0]).toEqual(ring[ring.length - 1]);
		expect(boxOutline(null).features).toEqual([]);
	});
});

describe('the box around a shape', () => {
	it('covers every polygon in it', () => {
		const shape: FeatureCollection = {
			type: 'FeatureCollection',
			features: [
				{
					type: 'Feature',
					properties: {},
					geometry: {
						type: 'Polygon',
						coordinates: [
							[
								[33, 3],
								[40, 15],
								[33, 3]
							]
						]
					}
				},
				{
					type: 'Feature',
					properties: {},
					geometry: {
						type: 'MultiPolygon',
						coordinates: [
							[
								[
									[48, 8],
									[45, 4],
									[48, 8]
								]
							]
						]
					}
				}
			]
		};
		expect(shapeBounds(shape)).toEqual(ethiopia);
	});

	it('is missing for a shape with no polygons', () => {
		expect(shapeBounds({ type: 'FeatureCollection', features: [] })).toBeNull();
	});
});

describe('comparing boxes', () => {
	it('counts touching edges as overlapping, and separate boxes as not', () => {
		expect(overlaps(ethiopia, { lat_min: 15, lat_max: 20, lon_min: 40, lon_max: 42 })).toBe(true);
		expect(overlaps(ethiopia, { lat_min: 8, lat_max: 30, lon_min: 68, lon_max: 97 })).toBe(false);
	});
});

describe('bounds read from detected metadata', () => {
	it('are trusted only when every edge is a number', () => {
		expect(parseBounds({ ...ethiopia, extra: 'kept out' })).toEqual(ethiopia);
		expect(parseBounds({ ...ethiopia, lat_max: '15' })).toBeNull();
		expect(parseBounds(null)).toBeNull();
	});
});
