import type { DataSource } from '$lib/api';
import { gridStep } from '$lib/source-coverage';

export type GridGroup = { step: number | null; sources: DataSource[] };
export type RegionGroup = { region: string | null; size: number; grids: GridGroup[] };

function groupBy<T, K>(items: T[], key: (item: T) => K): [K, T[]][] {
	const groups = items.reduce(
		(acc, item) => acc.set(key(item), [...(acc.get(key(item)) ?? []), item]),
		new Map<K, T[]>()
	);
	return [...groups];
}

function nullsLast<T>(compare: (a: T, b: T) => number) {
	return (a: T | null, b: T | null) =>
		a === null ? (b === null ? 0 : 1) : b === null ? -1 : compare(a, b);
}

// Regions alphabetically by label, grids finest first; sources with no region
// or no detected grid sort last. Sources keep their incoming order.
export function groupSources(
	sources: DataSource[],
	regionLabel: (regionId: string) => string
): RegionGroup[] {
	const byRegion = nullsLast<string>((a, b) => regionLabel(a).localeCompare(regionLabel(b)));
	const byStep = nullsLast<number>((a, b) => a - b);
	return groupBy(sources, (source) => source.region || null)
		.sort(([a], [b]) => byRegion(a, b))
		.map(([region, members]) => ({
			region,
			size: members.length,
			grids: groupBy(members, gridStep)
				.sort(([a], [b]) => byStep(a, b))
				.map(([step, grouped]) => ({ step, sources: grouped }))
		}));
}
