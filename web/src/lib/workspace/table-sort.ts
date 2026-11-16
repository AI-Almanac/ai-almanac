/** Ordering a table by one of its columns, the same way in every workspace table. */
export type SortDirection = 'ascending' | 'descending';

/** The column a table is ordered by; null leaves the table in its own order. */
export type TableSort<Key> = { key: Key; direction: SortDirection } | null;

function opposite(direction: SortDirection): SortDirection {
	return direction === 'ascending' ? 'descending' : 'ascending';
}

/**
 * A column heading pressed: the column's first direction (highest first for values,
 * A to Z for names), then the other, then the table's own order again.
 */
export function nextSort<Key>(
	current: TableSort<Key>,
	key: Key,
	first: SortDirection = 'descending'
): TableSort<Key> {
	if (current?.key !== key) return { key, direction: first };
	return current.direction === first ? { key, direction: opposite(first) } : null;
}

/** How a column is sorted, as its heading's `aria-sort` says it. */
export function sortState<Key>(sort: TableSort<Key>, key: Key): SortDirection | 'none' {
	return sort?.key === key ? sort.direction : 'none';
}

/** Rows ordered by the sorted column. A row without a value goes last either way; ties keep their order. */
export function sortRows<Row, Key>(
	rows: Row[],
	sort: TableSort<Key>,
	valueOf: (row: Row, key: Key) => number | string | null
): Row[] {
	if (!sort) return rows;
	const sign = sort.direction === 'ascending' ? 1 : -1;
	return [...rows].sort((a, b) => {
		const [left, right] = [valueOf(a, sort.key), valueOf(b, sort.key)];
		if (left === null || right === null) return Number(left === null) - Number(right === null);
		if (left === right) return 0;
		return (left < right ? -1 : 1) * sign;
	});
}
