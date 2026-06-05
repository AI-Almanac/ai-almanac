import { error } from '@sveltejs/kit';
import { getModelFamily } from '$lib/almanac/catalog';

export function load({ params }) {
	const model = getModelFamily(params.slug);
	if (!model) error(404, 'Model family not found');

	return { model };
}
