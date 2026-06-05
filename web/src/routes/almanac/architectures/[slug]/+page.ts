import { error } from '@sveltejs/kit';
import { getArchitecture } from '$lib/almanac/catalog';

export function load({ params }) {
	const architecture = getArchitecture(params.slug);
	if (!architecture) error(404, 'Architecture not found');

	return { architecture };
}
