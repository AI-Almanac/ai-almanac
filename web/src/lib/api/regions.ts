// ---- Regions -----------------------------------------------------------------
import { request } from './core';

export type Region = {
	id: string;
	display_name: string;
	romp_region: string;
	description: string;
	has_data: boolean;
	source_count: number;
	lat_min: number | null;
	lat_max: number | null;
	lon_min: number | null;
	lon_max: number | null;
	land_only: boolean;
	shp_only: boolean;
	is_builtin: boolean;
	boundary_iso: string | null;
};

export type BoundaryLevel = 'adm1' | 'adm2';

export type RegionBoundaryMetadata = {
	boundaryID: string | null;
	boundaryName: string | null;
	boundaryType: string | null;
	boundarySource: string | null;
	boundaryLicense: string | null;
	licenseSource: string | null;
};

export type RegionBoundaryResponse = {
	metadata: RegionBoundaryMetadata;
	geojson: unknown;
};

export type RegionWrite = {
	display_name: string;
	description: string;
	lat_min: number;
	lat_max: number;
	lon_min: number;
	lon_max: number;
	land_only: boolean;
};

export async function getRegions() {
	return request<Region[]>('/regions');
}

export async function createRegion(body: RegionWrite) {
	return request<Region>('/regions', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export async function updateRegion(id: string, body: RegionWrite) {
	return request<Region>(`/regions/${encodeURIComponent(id)}`, {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

export async function deleteRegion(id: string): Promise<void> {
	await request<void>(`/regions/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function getRegionBoundary(region: string, level: BoundaryLevel) {
	return request<RegionBoundaryResponse>(
		`/regions/${encodeURIComponent(region)}/boundaries/${encodeURIComponent(level)}`
	);
}
