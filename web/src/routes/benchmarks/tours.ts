import type { DriveStep } from 'driver.js';
import { queueTour } from '$lib/tour.svelte';

export function benchmarkResultsSteps(onNewBenchmark: () => void): DriveStep[] {
	return [
		{
			popover: {
				title: 'Benchmarking onset forecasts',
				description:
					'A benchmark scores how well each AI weather model predicted the start of the rainy season across a region, compared with observations. Deterministic metrics score a single forecast; probabilistic metrics score an ensemble. Let’s look around a finished run.'
			}
		},
		{
			element: '[data-tour="metric-map"]',
			popover: {
				title: 'Skill map',
				description:
					'Each cell is one grid point, coloured by the selected metric. Hover a cell to read its values.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="map-controls"]',
			popover: {
				title: 'Map controls',
				description:
					'Choose the metric, lead time, and model to display, or switch views to compare two models side by side.',
				side: 'left'
			}
		},
		{
			element: '[data-tour="results"] [role="tablist"]',
			popover: {
				title: 'All metrics',
				description:
					'Switch to All Metrics for every score by model and lead time, including the probabilistic metrics and skill curves.',
				side: 'bottom'
			}
		},
		{
			element: '[data-tour="metrics-tables"]',
			popover: {
				title: 'Metrics tables',
				description:
					'Below the map, each model’s raw metrics and quantiles are listed by lead window. Use the subregion filter to recompute them for a smaller latitude-longitude box.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="benchmark-summary"]',
			popover: {
				title: 'Benchmark summary',
				description:
					'Expand this to see exactly how the run was configured: models, forecast period, climatology, and onset detection parameters.',
				side: 'bottom',
				align: 'center'
			}
		},
		{
			element: '[data-tour="new-run"]',
			popover: {
				title: 'Run your own',
				description: 'Ready to benchmark the models and region you care about? Start here.',
				side: 'right',
				doneBtnText: 'Set up a benchmark',
				onDoneClick: (_element, _step, { driver }) => {
					driver.destroy();
					queueTour('benchmark-setup');
					onNewBenchmark();
				}
			}
		}
	];
}

export function benchmarkSetupSteps(openManualConfig: () => void): DriveStep[] {
	return [
		{
			element: '[data-tour="benchmark-plan"]',
			popover: {
				title: 'Benchmark plan',
				description:
					'This panel tracks your current setup. Once a region, ground truth dataset, and models are chosen it becomes Runnable and you can start the run.',
				side: 'left'
			}
		},
		{
			element: '[data-tour="setup-chat"]',
			popover: {
				title: 'Let the assistant set it up',
				description:
					'Describe the benchmark you want in plain language, for example “Compare monsoon onset skill over southern India”. The assistant fills in the plan and can explain any option.',
				side: 'right'
			}
		},
		{
			element: '[data-tour="manual-config"]',
			popover: {
				title: 'Or configure it yourself',
				description: 'Manual configuration exposes every setting directly. Let’s take a look.',
				side: 'left',
				onNextClick: (_element, _step, { driver }) => {
					openManualConfig();
					driver.moveNext();
				}
			}
		},
		{
			element: '[data-tour="config-plan"]',
			popover: {
				title: 'Inputs',
				description:
					'Pick the region, the observation dataset used as ground truth, and the forecast period to score.',
				side: 'bottom'
			}
		},
		{
			element: '[data-tour="config-models"]',
			popover: {
				title: 'Models',
				description:
					'Select the AI weather models to benchmark. Each one runs as its own job in the set.',
				side: 'bottom'
			}
		},
		{
			element: '[data-tour="config-windows"]',
			popover: {
				title: 'Model run windows',
				description:
					'Per-model forecast lengths and initialization days. The defaults are fine to start with.',
				side: 'bottom'
			}
		},
		{
			element: '[data-tour="config-shared"]',
			popover: {
				title: 'Shared settings',
				description:
					'How onset is detected (wet thresholds and spell lengths) and the climatology baseline. Defaults follow the standard definition; change them when you know you need to.',
				side: 'top'
			}
		}
	];
}
