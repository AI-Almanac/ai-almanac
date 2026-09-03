import type { DriveStep } from 'driver.js';
import { queueTour } from '$lib/tour.svelte';

export function forecastResultsSteps(onNewForecast: () => void): DriveStep[] {
	return [
		{
			popover: {
				title: 'Probabilistic onset forecasts',
				description:
					'A forecast runs a trained blend’s models forward from the latest conditions and combines them with the blend’s weights. The result is the probability that the rainy season has started, or will start, at each location. Let’s look at a finished one.'
			}
		},
		{
			element: '[data-tour="forecast-map"]',
			popover: {
				title: 'Forecast map',
				description:
					'Each cell is coloured by its onset probability for the selected forecast date. Use the date strip under the map to step through the season or play it as an animation.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="forecast-controls"]',
			popover: {
				title: 'Map controls',
				description:
					'Switch the view, choose the onset window to show, toggle administrative boundaries, and read the legend for the current colouring.',
				side: 'right'
			}
		},
		{
			element: '[data-tour="forecast-map"]',
			popover: {
				title: 'Look inside a cell',
				description:
					'Click any grid cell to open its season inspector: how the onset probability for that location evolves from one forecast date to the next.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="forecast-outputs"]',
			popover: {
				title: 'Outputs',
				description:
					'Download the forecast probabilities as CSV, along with the other files the run produced, to use outside the platform.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="new-run"]',
			popover: {
				title: 'Run your own',
				description: 'Have a trained blend? Issue a live forecast from it here.',
				side: 'right',
				doneBtnText: 'Set up a forecast',
				onDoneClick: (_element, _step, { driver }) => {
					driver.destroy();
					queueTour('forecast-setup');
					onNewForecast();
				}
			}
		}
	];
}

export function forecastSetupSteps(): DriveStep[] {
	return [
		{
			element: '[data-tour="forecast-blend"]',
			popover: {
				title: 'Blend',
				description:
					'Pick a completed blend. Its trained weights decide how the models are combined into the forecast.',
				side: 'right'
			}
		},
		{
			element: '[data-tour="forecast-init"]',
			popover: {
				title: 'Initialization data',
				description:
					'The observed conditions each model is started from. The default is fine to begin with.',
				side: 'right'
			}
		},
		{
			element: '[data-tour="forecast-models"]',
			waitForElement: 0,
			popover: {
				title: 'Forecast models',
				description:
					'Only the blend’s models that can run live are listed. Select the ones to include in this forecast.',
				side: 'right'
			}
		},
		{
			element: '[data-tour="forecast-run"]',
			popover: {
				title: 'Run forecast',
				description:
					'Inference runs in the background. The forecast appears in your list and can be updated each week as new conditions arrive.',
				side: 'top'
			}
		}
	];
}
