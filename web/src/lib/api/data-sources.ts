// ---- Data sources -----------------------------------------------------------
// Types are bound to the generated OpenAPI schema (api-types.gen.ts) so a
// backend contract change surfaces as a TS error here, not at runtime. This is
// the pattern to follow when touching the other api/ modules.
import type { components } from '../api-types.gen';
import { request } from './core';

export type DataSource = components['schemas']['DataSourceOut'];
export type DataSourceCreate = components['schemas']['DataSourceIn'];
export type DataSourceValidation = components['schemas']['DataSourceValidationOut'];

export async function listDataSources(kind?: 'obs' | 'model'): Promise<DataSource[]> {
	const q = kind ? `?kind=${kind}` : '';
	return request<DataSource[]>(`/data-sources${q}`);
}

export async function validateDataSource(body: DataSourceCreate): Promise<DataSourceValidation> {
	return request<DataSourceValidation>('/data-sources/validate', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export async function createDataSource(body: DataSourceCreate): Promise<DataSource> {
	return request<DataSource>('/data-sources', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export async function updateDataSource(
	id: string,
	body: Omit<DataSourceCreate, 'kind'>
): Promise<DataSource> {
	return request<DataSource>(`/data-sources/${id}`, {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

export async function revalidateDataSource(id: string): Promise<DataSource> {
	return request<DataSource>(`/data-sources/${id}/revalidate`, { method: 'POST' });
}

export async function deleteDataSource(id: string): Promise<void> {
	await request<void>(`/data-sources/${id}`, { method: 'DELETE' });
}
