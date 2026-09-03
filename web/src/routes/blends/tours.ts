import type { DriveStep } from 'driver.js';
import { queueTour } from '$lib/tour.svelte';

export function blendResultsSteps(onNewBlend: () => void): DriveStep[] {
	return [
		{
			popover: {
				title: 'Blending models',
				description:
					'A blend combines several AI weather models into one forecast, weighting each by how well it predicted onset in past seasons. The result is usually more skilful than any single model, and it is what the Forecasts page runs. Let’s look at a trained blend.'
			}
		},
		{
			element: '[data-tour="blend-metrics"]',
			popover: {
				title: 'Forecast skill',
				description:
					'How the blend scores against each of its models and against climatology. Below this table the same skill is broken down by forecast lead.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="blend-maps"]',
			popover: {
				title: 'Skill by grid point',
				description:
					'The same skill mapped across the region, so you can see where the blend helps most and where a single model still wins.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="blend-outputs"]',
			popover: {
				title: 'Weights and outputs',
				description:
					'Download the trained weights and scoring files to use the blend outside the platform.',
				side: 'top',
				align: 'center'
			}
		},
		{
			element: '[data-tour="blend-chat"]',
			popover: {
				title: 'Ask about the results',
				description:
					'The assistant can explain these scores, compare the models, or set up a new blend based on what it sees here.',
				side: 'left'
			}
		},
		{
			element: '[data-tour="new-run"]',
			popover: {
				title: 'Train your own',
				description: 'Ready to blend the models you care about? Start here.',
				side: 'right',
				doneBtnText: 'Set up a blend',
				onDoneClick: (_element, _step, { driver }) => {
					driver.destroy();
					queueTour('blend-setup');
					onNewBlend();
				}
			}
		}
	];
}

export function blendSetupSteps(): DriveStep[] {
	return [
		{
			element: '[data-tour="blend-obs"]',
			popover: {
				title: 'Observations',
				description:
					'Pick the ground-truth rainfall dataset. It scores the models and sets the climatology baseline the blend is measured against.',
				side: 'right'
			}
		},
		{
			element: '[data-tour="blend-models"]',
			popover: {
				title: 'Forecast models',
				description:
					'Choose two or more models to combine. Your benchmark results are a good guide to which ones deserve a place.',
				side: 'right'
			}
		},
		{
			element: '[data-tour="blend-years"]',
			popover: {
				title: 'Training and holdout years',
				description:
					'Training years teach the blend its weights. Holdout years give an honest score on seasons it never saw.',
				side: 'top'
			}
		},
		{
			element: '[data-tour="blend-train"]',
			popover: {
				title: 'Train',
				description:
					'Training runs in the background. The blend appears in your list with its skill and weights when it finishes.',
				side: 'top'
			}
		},
		{
			element: '[data-tour="setup-chat"]',
			popover: {
				title: 'Or let the assistant do it',
				description:
					'Describe the blend you want, or ask what any option means, and the assistant fills in the form for you.',
				side: 'right'
			}
		}
	];
}
