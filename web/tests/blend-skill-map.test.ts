import { describe, expect, it } from 'vitest';

import type { BlendCellGrid } from '../src/lib/api';
import {
	buildSkillCells,
	cellOpacity,
	halfCell,
	rampPosition,
	shareBeatingBaseline,
	skillBounds
} from '../src/routes/blends/blend-skill-map';

function grid(overrides: Partial<BlendCellGrid> = {}): BlendCellGrid {
	return {
		metric: 'ranked_probability_skill_score',
		label: 'Ranked Probability Skill Score',
		lats: [10, 10.25],
		lons: [33, 33.25],
		values: [
			[0.4, -0.4],
			[0.1, null]
		],
		counts: [
			[24, 24],
			[4, null]
		],
		scale_max_abs: 0.4,
		value_min: -0.4,
		value_max: 0.4,
		clipped: 0,
		...overrides
	};
}

describe('rampPosition', () => {
	it('puts zero skill at the midpoint so colour families mean beat-or-lose', () => {
		expect(rampPosition(0, 0.5)).toBe(0.5);
	});

	it('maps the extremes to the ramp ends', () => {
		expect(rampPosition(0.5, 0.5)).toBe(1);
		expect(rampPosition(-0.5, 0.5)).toBe(0);
	});

	it('clamps beyond the extent rather than running off the ramp', () => {
		expect(rampPosition(99, 0.5)).toBe(1);
		expect(rampPosition(-99, 0.5)).toBe(0);
	});

	it('falls back to neutral when there is no usable extent', () => {
		expect(rampPosition(0.3, 0)).toBe(0.5);
	});
});

describe('cellOpacity', () => {
	it('draws well-sampled points at full strength', () => {
		expect(cellOpacity(50, 10)).toBeGreaterThan(0.8);
		expect(cellOpacity(10, 10)).toBeGreaterThan(0.8);
	});

	it('fades points below the floor in proportion to their sample size', () => {
		// Muted rather than dropped: hiding them would misstate coverage.
		const thin = cellOpacity(2, 10);
		const thicker = cellOpacity(8, 10);
		expect(thin).toBeLessThan(thicker);
		expect(thicker).toBeLessThan(cellOpacity(10, 10));
	});

	it('fades points with no count at all', () => {
		expect(cellOpacity(null, 10)).toBeLessThan(cellOpacity(10, 10));
	});
});

describe('halfCell', () => {
	it('derives half-widths from coordinate spacing', () => {
		expect(halfCell(grid(), null)).toEqual({ dlat: 0.125, dlon: 0.125 });
	});

	it('uses the reported cell size when an axis has a single coordinate', () => {
		// A region one column wide has no spacing of its own to measure.
		const single = grid({ lons: [33], values: [[0.4], [0.1]], counts: [[24], [4]] });
		expect(halfCell(single, 0.5).dlon).toBe(0.25);
	});

	it('falls back to a quarter degree when nothing else is known', () => {
		const single = grid({ lats: [10], lons: [33], values: [[0.4]], counts: [[24]] });
		expect(halfCell(single, null)).toEqual({ dlat: 0.125, dlon: 0.125 });
	});
});

describe('buildSkillCells', () => {
	it('emits one square per scored point and skips empty ones', () => {
		const cells = buildSkillCells(grid(), { minObservations: 10, cellSizeDeg: 0.25 });
		expect(cells.features).toHaveLength(3);
		const ring = cells.features[0].geometry.coordinates[0];
		// Closed ring centred on the point.
		expect(ring).toHaveLength(5);
		expect(ring[0]).toEqual(ring[4]);
	});

	it('colours opposite signs differently and centres neutral', () => {
		const cells = buildSkillCells(grid(), { minObservations: 10, cellSizeDeg: 0.25 });
		const better = cells.features.find((f) => f.properties.skill === 0.4);
		const worse = cells.features.find((f) => f.properties.skill === -0.4);
		expect(better?.properties.color).not.toBe(worse?.properties.color);
	});

	it('fades a point resting on few observations', () => {
		const cells = buildSkillCells(grid(), { minObservations: 10, cellSizeDeg: 0.25 });
		const thin = cells.features.find((f) => f.properties.observations === 4);
		const solid = cells.features.find((f) => f.properties.observations === 24);
		expect(thin!.properties.opacity).toBeLessThan(solid!.properties.opacity);
	});

	it('flags points past the clipped scale so the tooltip can say so', () => {
		const outlier = grid({
			values: [
				[0.4, -12],
				[null, null]
			],
			counts: [
				[24, 24],
				[null, null]
			]
		});
		const cells = buildSkillCells(outlier, { minObservations: 10, cellSizeDeg: 0.25 });
		expect(cells.features.find((f) => f.properties.skill === -12)!.properties.clipped).toBe(true);
		expect(cells.features.find((f) => f.properties.skill === 0.4)!.properties.clipped).toBe(false);
	});
});

describe('skillBounds', () => {
	it('covers the drawn squares, not just their centres', () => {
		expect(skillBounds(grid(), 0.25)).toEqual([
			[32.875, 9.875],
			[33.375, 10.375]
		]);
	});

	it('returns null when there is nothing to frame', () => {
		expect(skillBounds(grid({ lats: [], lons: [] }), 0.25)).toBeNull();
	});
});

describe('shareBeatingBaseline', () => {
	it('counts only scored points', () => {
		expect(shareBeatingBaseline(grid())).toEqual({ better: 2, total: 3 });
	});

	it('treats exactly matching climatology as not beating it', () => {
		const tied = grid({ values: [[0]], counts: [[24]], lats: [10], lons: [33] });
		expect(shareBeatingBaseline(tied)).toEqual({ better: 0, total: 1 });
	});
});
