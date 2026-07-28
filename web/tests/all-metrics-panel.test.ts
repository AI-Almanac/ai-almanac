/**
 * AllMetricsPanel and the ResultsViewer tab split.
 *
 * NOTE: this file should be renamed to `all-metrics-panel.test.ts`. It could not
 * be renamed in place when SkillScoresPanel was replaced by AllMetricsPanel.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import AllMetricsPanel from '../src/lib/components/AllMetricsPanel.svelte';
import ResultsViewer from '../src/lib/components/ResultsViewer.svelte';
import type { Job, JobMetrics, JobSkillScores } from '../src/lib/api';

// uPlot and MapLibre both need real layout/canvas, which jsdom doesn't provide.
//
// Mocked with a real component rather than `{ default: {} }`: an object throws
// "default is not a function" on instantiation, and when that happens inside an
// effect Vitest reports it as an unhandled error while the test still passes.
// The factory must be inline: vi.mock calls are hoisted above const declarations,
// so a shared `stub` reference would be read before initialization.
vi.mock('../src/lib/components/SkillCurveChart.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/MetricMap.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));
vi.mock('../src/lib/components/MetricsTable.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

const api = vi.hoisted(() => ({
	getJobSkillScores: vi.fn(),
	getJobMetrics: vi.fn(),
	getMetricDefinitions: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return {
		...actual,
		getJobSkillScores: api.getJobSkillScores,
		getJobMetrics: api.getJobMetrics,
		getMetricDefinitions: api.getMetricDefinitions
	};
});

const DEFINITIONS = [
	{
		id: 'brier_skill_score',
		label: 'Brier Skill Score',
		abbreviation: 'BSS',
		unit: 'dimensionless',
		lower_is_better: false
	},
	{
		id: 'auc',
		label: 'Area Under ROC Curve',
		abbreviation: 'AUC',
		unit: 'fraction',
		lower_is_better: false
	}
];

function job(id: string, overrides: Partial<Job> = {}): Job {
	return {
		id,
		status: 'complete',
		model_name: 'aifsens2',
		model_display_name: 'AIFSENS2',
		...overrides
	} as Job;
}

const EMPTY_METRICS: JobMetrics = {
	job_id: 'x',
	windows: [],
	grid: null,
	bbox: null
} as JobMetrics;

function skill(jobId: string, auc = 0.82): JobSkillScores {
	const window = (name: string, min: number, max: number) => ({
		model: 'aifsens2',
		window: name,
		overall: { brier_skill_score: 0.21, auc, auc_ref: 0.51 },
		bins: [
			{
				bin: `Days ${min}-${max}`,
				label: `${min}-${max}`,
				lead_day_min: min,
				lead_day_max: max,
				brier_skill_score: 0.31,
				auc,
				auc_ref: 0.52,
				brier_score_forecast: 0.09,
				brier_score_climatology: 0.13
			}
		]
	});
	return { job_id: jobId, windows: [window('1-15', 1, 5), window('16-30', 16, 20)] };
}

describe('AllMetricsPanel', () => {
	it('shows both lead-time windows at once', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockResolvedValue(skill('p-1'));
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(AllMetricsPanel, { props: { jobs: [job('p-1')] } });

		// Both window headers are present without switching anything.
		await waitFor(() => {
			expect(screen.getByText('Days 1–15')).toBeTruthy();
		});
		expect(screen.getByText('Days 16–30')).toBeTruthy();
	});

	it('spells metric names out rather than abbreviating them', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockResolvedValue(skill('p-2'));
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(AllMetricsPanel, { props: { jobs: [job('p-2')] } });

		await waitFor(() => {
			expect(screen.getByText('Brier Skill Score')).toBeTruthy();
		});
		expect(screen.getByText('Area Under ROC Curve')).toBeTruthy();
		expect(screen.queryByText('BSS')).toBeNull();
		expect(screen.queryByText('AUC')).toBeNull();
	});

	it('renders scores as decimals, not percentages', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockResolvedValue(skill('p-3', 0.82));
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(AllMetricsPanel, { props: { jobs: [job('p-3')] } });

		await waitFor(() => {
			expect(screen.getAllByText('0.820').length).toBeGreaterThan(0);
		});
		expect(screen.queryByText('82.0%')).toBeNull();
	});

	it('names the metrics the benchmark does not compute', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockResolvedValue(skill('p-4'));
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(AllMetricsPanel, { props: { jobs: [job('p-4')] } });

		await waitFor(() => {
			expect(screen.getByText(/Reliability/)).toBeTruthy();
		});
		// Absence has to be visible, or users assume discrimination covers calibration.
		expect(screen.getByText(/not a passing score/)).toBeTruthy();
	});

	it('explains an empty run set rather than rendering nothing', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockResolvedValue({ job_id: 'e-1', windows: [] });
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(AllMetricsPanel, { props: { jobs: [job('e-1')] } });

		await waitFor(() => {
			expect(screen.getByText(/No metrics found for this run set/)).toBeTruthy();
		});
	});

	it('surfaces a fetch failure distinctly from an empty result', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockRejectedValue(new Error('boom'));
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(AllMetricsPanel, { props: { jobs: [job('err-1')] } });

		await waitFor(() => {
			expect(screen.getByText(/Failed to load metrics: boom/)).toBeTruthy();
		});
	});
});

describe('ResultsViewer tabs', () => {
	it('defaults to the map and switches to all metrics', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockResolvedValue(skill('t-1'));
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(ResultsViewer, { props: { jobs: [job('t-1')] } });

		const mapTab = screen.getByRole('tab', { name: 'Map' });
		const metricsTab = screen.getByRole('tab', { name: 'All Metrics' });

		expect(mapTab.getAttribute('aria-selected')).toBe('true');
		expect(screen.getByText(/No spatial data available/)).toBeTruthy();

		await fireEvent.click(metricsTab);

		expect(metricsTab.getAttribute('aria-selected')).toBe('true');
		expect(screen.queryByText(/No spatial data available/)).toBeNull();
		await waitFor(() => {
			expect(screen.getByText('Brier Skill Score')).toBeTruthy();
		});
	});

	it('offers the metrics tab even for a run set with no probabilistic jobs', async () => {
		api.getJobMetrics.mockResolvedValue(EMPTY_METRICS);
		api.getJobSkillScores.mockResolvedValue({ job_id: 'd-1', windows: [] });
		api.getMetricDefinitions.mockResolvedValue(DEFINITIONS);

		render(ResultsViewer, { props: { jobs: [job('d-1')] } });

		// Hiding it would make the feature undiscoverable; the panel explains instead.
		expect(screen.getByRole('tab', { name: 'All Metrics' })).toBeTruthy();
	});
});
