// ---- Places -----------------------------------------------------------------
// Countries and the administrative units inside them. Reference data: looked up,
// never created.
import { request } from './core';
import type { components } from '../api-types.gen';
import type { BoundaryLevel, RegionBoundaryResponse } from './regions';

export type Country = components['schemas']['CountryOut'];

let countries: Promise<Country[]> | null = null;

/** Every country a place can be picked in. Asked for once per page load. */
export function getCountries(): Promise<Country[]> {
	countries ??= request<Country[]>('/places/countries').catch((error) => {
		countries = null;
		throw error;
	});
	return countries;
}

export async function getPlaceBoundary(iso: string, level: BoundaryLevel) {
	return request<RegionBoundaryResponse>(
		`/places/${encodeURIComponent(iso)}/boundaries/${encodeURIComponent(level)}`
	);
}
