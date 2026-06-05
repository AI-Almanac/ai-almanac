import { error } from '@sveltejs/kit';
import { getDataset } from '$lib/almanac/catalog';

export function load({ params }) {
	const dataset = getDataset(params.slug);
	if (!dataset) error(404, 'Dataset not found');

	return { dataset };
}
