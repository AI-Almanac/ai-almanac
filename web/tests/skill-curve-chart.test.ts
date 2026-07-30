/**
 * SkillCurveChart's initial series visibility.
 *
 * The chart is shared by the benchmarks results view and the blend results
 * panel, and only the latter wants a subset drawn on arrival, so the two paths
 * through `defaultVisible` are worth pinning.
 */
import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

// uPlot needs real layout and canvas, which jsdom doesn't provide. Constructed
// inside an effect, a throw there surfaces as an unhandled error while the test
// still passes, so the stub keeps a real failure from hiding behind noise.
vi.mock('uplot', () => ({
	default: class {
		over = document.createElement('div');
		cursor: Record<string, unknown> = {};
		data: unknown[] = [];
		series: unknown[] = [];
		setSize() {}
		destroy() {}
		valToPos() {
			return 0;
		}
	}
}));

import SkillCurveChart from '../src/lib/components/SkillCurveChart.svelte';
import type { SkillCurveSeries } from '../src/lib/skill-series';

const leads = [
	{ day: 0, label: 'Week 1' },
	{ day: 1, label: 'Week 2' }
];

const series: SkillCurveSeries[] = [
	{ key: 'blended_model', label: 'Blend', color: '#111', values: [0.1, 0.2] },
	{ key: 'unc_clim_raw', label: 'Traditional Climatology', color: '#222', values: [0, 0] },
	{ key: 'aifs_raw', label: 'AIFS (raw)', color: '#333', values: [-1.2, -0.9] }
];

function toggleState() {
	return Object.fromEntries(
		screen
			.getAllByRole('button')
			.map((button) => [button.textContent?.trim(), button.getAttribute('aria-pressed')])
	);
}

function renderChart(props: Record<string, unknown> = {}) {
	render(SkillCurveChart, {
		title: 'Brier Skill Score',
		leads,
		series,
		referenceValue: 0,
		referenceLabel: 'No skill',
		caption: 'caption',
		...props
	});
}

describe('SkillCurveChart initial visibility', () => {
	it('draws every series when the caller expresses no preference', () => {
		renderChart();
		expect(toggleState()).toEqual({
			Blend: 'true',
			'Traditional Climatology': 'true',
			'AIFS (raw)': 'true'
		});
	});

	it('draws only the series the caller asks for', () => {
		renderChart({ defaultVisible: (key: string) => key !== 'aifs_raw' });
		expect(toggleState()).toEqual({
			Blend: 'true',
			'Traditional Climatology': 'true',
			'AIFS (raw)': 'false'
		});
	});

	it('still offers the hidden series as a toggle rather than dropping it', () => {
		renderChart({ defaultVisible: () => false });
		expect(screen.getAllByRole('button')).toHaveLength(3);
		expect(screen.getByText('Select at least one model to show the plot.')).toBeTruthy();
	});
});
