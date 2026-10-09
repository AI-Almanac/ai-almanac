import { describe, expect, it } from 'vitest';
import type { DataSource } from '../src/lib/api';
import { groupSources } from '../src/routes/data-sources/source-groups';

function source(id: string, region: string | null, gridStep?: number): DataSource {
	const metadata = gridStep === undefined ? {} : { grid_step_deg: gridStep };
	return { id, region, metadata } as unknown as DataSource;
}

const labels: Record<string, string> = { india: 'India', ethiopia: 'Ethiopia' };
const label = (id: string) => labels[id] ?? id;

function shape(sources: DataSource[]) {
	return groupSources(sources, label).map((group) => ({
		region: group.region,
		grids: group.grids.map((grid) => [grid.step, grid.sources.map((s) => s.id)])
	}));
}

describe('groupSources', () => {
	it('groups by region label, then by grid with the finest grid first', () => {
		const sources = [
			source('aifs-india', 'india', 2),
			source('aifs-ethiopia', 'ethiopia', 0.25),
			source('aifs-v2-india', 'india', 0.25),
			source('fuxi-india', 'india', 2)
		];

		expect(shape(sources)).toEqual([
			{ region: 'ethiopia', grids: [[0.25, ['aifs-ethiopia']]] },
			{
				region: 'india',
				grids: [
					[0.25, ['aifs-v2-india']],
					[2, ['aifs-india', 'fuxi-india']]
				]
			}
		]);
	});

	it('puts sources with no region or no detected grid last', () => {
		const sources = [
			source('unplaced', null, 2),
			source('legacy-india', 'india'),
			source('aifs-india', 'india', 2)
		];

		expect(shape(sources)).toEqual([
			{
				region: 'india',
				grids: [
					[2, ['aifs-india']],
					[null, ['legacy-india']]
				]
			},
			{ region: null, grids: [[2, ['unplaced']]] }
		]);
	});

	it('counts every source in a region', () => {
		const [india] = groupSources([source('a', 'india', 2), source('b', 'india', 0.25)], label);
		expect(india.size).toBe(2);
	});
});
