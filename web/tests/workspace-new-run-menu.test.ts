import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import NewRunMenu from '../src/lib/components/workspace/NewRunMenu.svelte';
import { menuMove, newRunEntries, startingPoints } from '../src/lib/workspace/new-run-menu';

describe('what New offers', () => {
	it('opens every kind of run, and the runs that can be added, inside the workspace', () => {
		const entries = newRunEntries(true);

		expect(entries.map((entry) => entry.label)).toEqual([
			'Benchmark',
			'Blend',
			'Forecast',
			'Add an existing run'
		]);
		expect(entries.map((entry) => entry.panel)).toEqual([
			'benchmark_setup',
			'blend_setup',
			'forecast_setup',
			'add_existing_run'
		]);
	});

	it('leaves forecasts out for someone who cannot make them', () => {
		expect(newRunEntries(false).map((entry) => entry.key)).toEqual([
			'benchmark',
			'blend',
			'add-existing'
		]);
	});

	it('leads an empty workspace with a benchmark, a blend and an existing run', () => {
		const points = startingPoints(newRunEntries(true));

		expect(points.map((entry) => entry.startLabel)).toEqual([
			'Run a benchmark',
			'Train a blend',
			'Add an existing run'
		]);
	});
});

describe('NewRunMenu', () => {
	it('opens on New, hands back the chosen action and closes', async () => {
		const onChoose = vi.fn();
		render(NewRunMenu, { entries: newRunEntries(true), onChoose });
		expect(screen.queryByRole('menu')).toBeNull();

		await fireEvent.click(screen.getByRole('button', { name: /New/ }));
		expect(screen.queryByRole('link')).toBeNull();
		await fireEvent.click(screen.getByRole('menuitem', { name: 'Add an existing run' }));

		expect(onChoose).toHaveBeenCalledWith('add_existing_run');
		expect(screen.queryByRole('menu')).toBeNull();
	});

	it('closes without choosing when Escape is pressed', async () => {
		const onChoose = vi.fn();
		render(NewRunMenu, { entries: newRunEntries(false), onChoose, compact: true });

		const trigger = screen.getByRole('button', { name: 'New' });
		await fireEvent.click(trigger);
		await fireEvent.keyDown(await screen.findByRole('menu'), { key: 'Escape' });

		expect(screen.queryByRole('menu')).toBeNull();
		expect(document.activeElement).toBe(trigger);
		expect(onChoose).not.toHaveBeenCalled();
	});

	it('moves focus to the first entry on opening and between entries with the arrow keys, Home and End', async () => {
		render(NewRunMenu, { entries: newRunEntries(true), onChoose: vi.fn() });
		await fireEvent.click(screen.getByRole('button', { name: /New/ }));
		const menu = await screen.findByRole('menu');
		const entries = screen.getAllByRole('menuitem');
		await waitFor(() => expect(document.activeElement).toBe(entries[0]));

		await fireEvent.keyDown(menu, { key: 'ArrowDown' });
		expect(document.activeElement).toBe(entries[1]);
		await fireEvent.keyDown(menu, { key: 'End' });
		expect(document.activeElement).toBe(entries.at(-1));
		await fireEvent.keyDown(menu, { key: 'ArrowDown' });
		expect(document.activeElement).toBe(entries[0]);
		await fireEvent.keyDown(menu, { key: 'ArrowUp' });
		expect(document.activeElement).toBe(entries.at(-1));
		await fireEvent.keyDown(menu, { key: 'Home' });
		expect(document.activeElement).toBe(entries[0]);
	});

	it('closes behind Tab, and returns to New after a choice', async () => {
		const onChoose = vi.fn();
		render(NewRunMenu, { entries: newRunEntries(true), onChoose });
		const trigger = screen.getByRole('button', { name: /New/ });

		await fireEvent.click(trigger);
		await fireEvent.keyDown(await screen.findByRole('menu'), { key: 'Tab' });
		expect(screen.queryByRole('menu')).toBeNull();

		await fireEvent.click(trigger);
		await fireEvent.click(await screen.findByRole('menuitem', { name: 'Benchmark' }));
		expect(onChoose).toHaveBeenCalledWith('benchmark_setup');
		expect(document.activeElement).toBe(trigger);
	});

	it('wraps at both ends and ignores keys that move nothing', () => {
		expect(menuMove('ArrowDown', 3, 4)).toBe(0);
		expect(menuMove('ArrowUp', 0, 4)).toBe(3);
		expect(menuMove('a', 1, 4)).toBeNull();
		expect(menuMove('ArrowDown', 0, 0)).toBeNull();
	});
});
