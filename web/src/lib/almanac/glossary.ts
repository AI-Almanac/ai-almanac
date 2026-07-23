import type { AlmanacReference } from './content';

export type GlossaryCategory =
	| 'AI weather prediction'
	| 'Climate'
	| 'Data'
	| 'Evaluation'
	| 'Forecasting';

export type GlossaryTerm = {
	slug: string;
	term: string;
	category: GlossaryCategory;
	shortDefinition: string;
	definition: string;
	relatedTerms: string[];
	references: AlmanacReference[];
};

export const glossaryTerms: GlossaryTerm[] = [
	{
		slug: 'ai-weather-prediction',
		term: 'AI Weather Prediction',
		category: 'AI weather prediction',
		shortDefinition:
			'Weather forecasting with machine-learned models trained on historical analyses.',
		definition:
			'AI weather prediction models learn forecast behavior from historical atmospheric data rather than solving the full numerical weather prediction system directly at inference time.',
		relatedTerms: ['Numerical weather prediction', 'Reanalysis'],
		references: []
	},
	{
		slug: 'reanalysis',
		term: 'Reanalysis',
		category: 'Data',
		shortDefinition: 'A gridded reconstruction of past atmospheric conditions.',
		definition:
			'Reanalysis datasets combine observations with a weather model to produce spatially and temporally complete estimates of historical atmospheric state.',
		relatedTerms: ['ERA5', 'Training data'],
		references: []
	},
	{
		slug: 'forecast-lead-time',
		term: 'Forecast Lead Time',
		category: 'Forecasting',
		shortDefinition: 'The time between forecast initialization and the valid forecast time.',
		definition:
			'Lead time describes how far into the future a forecast is being evaluated, such as day 1, day 7, or week 3 after initialization.',
		relatedTerms: ['Initialization', 'Forecast range'],
		references: []
	}
];
