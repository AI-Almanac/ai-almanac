export type EventTypeStatus = 'active' | 'coming_soon';

export interface EventType {
	id: string;
	name: string;
	shortName: string;
	description: string;
	regions: string[];
	status: EventTypeStatus;
	definition: string;
}

export const EVENT_TYPES: EventType[] = [
	{
		id: 'monsoon_onset',
		name: 'Monsoon Onset',
		shortName: 'Monsoon',
		description:
			'Evaluate model skill in predicting the calendar date of seasonal monsoon onset using region-specific meteorological definitions.',
		regions: ['India', 'Ethiopia'],
		status: 'active',
		definition:
			'India: Modified Moron–Robertson (MOK-anchored). Ethiopia: ICPAC 3-day accumulation threshold.'
	},
	{
		id: 'monsoon_cessation',
		name: 'Monsoon Cessation',
		shortName: 'Cessation',
		description:
			'Evaluate model skill in predicting the end of the monsoon season using region-specific withdrawal definitions.',
		regions: ['Coming soon'],
		status: 'coming_soon',
		definition: ''
	},
	{
		id: 'heatwave_onset',
		name: 'Heat Wave Onset',
		shortName: 'Heat Wave',
		description:
			'Benchmark forecasts of heat wave onset defined by sustained anomalous temperature thresholds.',
		regions: ['Coming soon'],
		status: 'coming_soon',
		definition: ''
	},
	{
		id: 'custom',
		name: 'Custom Event',
		shortName: 'Custom',
		description:
			"Define your own human-centric event metric with AI assistance. Describe what matters to your community — crop planting windows, flood risk days, school closure thresholds — and we'll help you build a rigorous benchmark around it.",
		regions: ['Coming soon'],
		status: 'coming_soon',
		definition: ''
	}
];
