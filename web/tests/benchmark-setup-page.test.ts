import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../src/lib/components/FocusAreaMap.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

import { BenchmarkStore } from '../src/lib/benchmarks.svelte';
import BenchmarkSetup from '../src/routes/benchmarks/BenchmarkSetup.svelte';

function renderSetup(props: { initialManualOpen?: boolean } = {}) {
	return render(BenchmarkSetup, {
		store: new BenchmarkStore(),
		regions: [],
		datasets: [],
		dataLoaded: true,
		parameterDefaults: null,
		guardrails: null,
		onSubmitted: vi.fn(),
		...props
	});
}

describe('setting up a benchmark without the assistant', () => {
	it('shows the plan beside a way into the full settings', async () => {
		renderSetup();

		expect(screen.getByRole('heading', { name: 'Benchmark plan' })).toBeTruthy();
		expect(screen.queryByRole('dialog')).toBeNull();

		await fireEvent.click(screen.getByRole('button', { name: 'Open benchmark settings' }));

		expect(screen.getByRole('dialog', { name: 'Benchmark settings' })).toBeTruthy();
	});

	it('opens the full settings straight away when they were asked for', () => {
		renderSetup({ initialManualOpen: true });

		expect(screen.getByRole('dialog', { name: 'Benchmark settings' })).toBeTruthy();
	});
});
