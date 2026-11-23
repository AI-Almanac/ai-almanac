// ---- Workspace settings -------------------------------------------------------
// The defaults that frame every run in a workspace; today, its season.
import type { components } from '../api-types.gen';
import { request } from './core';

export type WorkspaceSettings = components['schemas']['WorkspaceSettings'];
export type SeasonWindow = components['schemas']['SeasonWindow'];

/** The season a new workspace works in unless it sets its own: the one usual in `country`. */
export async function getDefaultSeason(country: string | null): Promise<SeasonWindow> {
	const query = country ? `?country=${encodeURIComponent(country)}` : '';
	return request<SeasonWindow>(`/workspaces/default-season${query}`);
}

/** The settings a run here uses today: unset ones filled with the platform defaults. */
export async function getEffectiveWorkspaceSettings(
	workspaceId: string
): Promise<WorkspaceSettings> {
	return request<WorkspaceSettings>(`/workspaces/${workspaceId}/settings/effective`);
}

/** Change the settings the patch names; `null` clears one back to the platform default. */
export async function updateWorkspaceSettings(
	workspaceId: string,
	patch: WorkspaceSettings
): Promise<WorkspaceSettings> {
	return request<WorkspaceSettings>(`/workspaces/${workspaceId}/settings`, {
		method: 'PATCH',
		body: JSON.stringify(patch)
	});
}
