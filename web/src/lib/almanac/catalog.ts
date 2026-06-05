import { architectures, datasets, modelFamilies } from './content';
import { glossaryTerms } from './glossary';
import type { WeatherArchitecture, WeatherDataset, ModelFamily } from './content';

export type AlmanacSection = {
	slug: 'models' | 'datasets' | 'architectures' | 'glossary';
	title: string;
	description: string;
	href: string;
	count: number;
};

export const almanacSections: AlmanacSection[] = [
	{
		slug: 'models',
		title: 'Model families',
		description: 'Forecast model lineages, checkpoints, training data, and benchmark caveats.',
		href: '/almanac/models',
		count: modelFamilies.length
	},
	{
		slug: 'datasets',
		title: 'Datasets',
		description: 'Training, validation, reanalysis, satellite, and benchmark observation datasets.',
		href: '/almanac/datasets',
		count: datasets.length
	},
	{
		slug: 'architectures',
		title: 'Architectures',
		description: 'Implementation-neutral summaries of the modeling approaches represented here.',
		href: '/almanac/architectures',
		count: architectures.length
	},
	{
		slug: 'glossary',
		title: 'Glossary',
		description: 'Climate, forecasting, data, evaluation, and AI weather prediction terminology.',
		href: '/almanac/glossary',
		count: glossaryTerms.length
	}
];

export function getModelFamily(slug: string): ModelFamily | undefined {
	return modelFamilies.find((model) => model.slug === slug);
}

export function getDataset(slug: string): WeatherDataset | undefined {
	return datasets.find((dataset) => dataset.slug === slug);
}

export function getArchitecture(slug: string): WeatherArchitecture | undefined {
	return architectures.find((architecture) => architecture.slug === slug);
}
