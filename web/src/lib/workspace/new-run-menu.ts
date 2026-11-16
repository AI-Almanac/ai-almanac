/** What the workspace's "New" menu offers, and how the keyboard moves through it. */
import type { PanelName } from './panels';

export type NewRunEntry = {
	key: string;
	/** The entry in the "New" menu. */
	label: string;
	/** The same entry as a starting point for an empty workspace; null when it is not one. */
	startLabel: string | null;
	/** The panel the entry opens. */
	panel: PanelName;
};

export function newRunEntries(canForecast: boolean): NewRunEntry[] {
	const entries: NewRunEntry[] = [
		{
			key: 'benchmark',
			label: 'Benchmark',
			startLabel: 'Run a benchmark',
			panel: 'benchmark_setup'
		},
		{
			key: 'blend',
			label: 'Blend',
			startLabel: 'Train a blend',
			panel: 'blend_setup'
		},
		{
			key: 'forecast',
			label: 'Forecast',
			startLabel: null,
			panel: 'forecast_setup'
		},
		{
			key: 'add-existing',
			label: 'Add an existing run',
			startLabel: 'Add an existing run',
			panel: 'add_existing_run'
		}
	];
	return entries.filter((entry) => canForecast || entry.key !== 'forecast');
}

/**
 * Where a key moves focus among a menu's `count` items from the one at `index`:
 * Up and Down wrap, Home and End jump. Null for a key that moves nothing.
 */
export function menuMove(key: string, index: number, count: number): number | null {
	if (count === 0) return null;
	if (key === 'ArrowDown') return (index + 1) % count;
	if (key === 'ArrowUp') return (Math.max(index, 0) - 1 + count) % count;
	if (key === 'Home') return 0;
	if (key === 'End') return count - 1;
	return null;
}

/** The entries an empty workspace leads with. */
export function startingPoints(entries: NewRunEntry[]): NewRunEntry[] {
	return entries.filter((entry) => entry.startLabel !== null);
}
