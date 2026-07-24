// ---- Settings ---------------------------------------------------------------
import { request } from './core';

export interface SettingsField {
	name: string;
	label: string;
	description: string;
	type: 'string' | 'int' | 'float' | 'bool';
	default: unknown;
	sensitive: boolean;
	restart_required: boolean;
	editable: boolean;
	multiline: boolean;
}

export interface SettingsGroup {
	name: string;
	fields: SettingsField[];
}

export interface SettingsSchema {
	deployment_mode: string;
	groups: SettingsGroup[];
}

// Non-sensitive field values, plus per-secret configured/not flags. Secret
// plaintext is never sent by the server.
export interface SettingsState {
	values: Record<string, unknown>;
	secrets: Record<string, boolean>;
}

export async function getSettingsSchema(): Promise<SettingsSchema> {
	return request<SettingsSchema>('/settings/schema');
}

export async function getSettings(): Promise<SettingsState> {
	return request<SettingsState>('/settings');
}

export async function patchSettings(values: Record<string, unknown>): Promise<SettingsState> {
	return request<SettingsState>('/settings', {
		method: 'PATCH',
		body: JSON.stringify({ values })
	});
}

export async function getConfigYamlPath(): Promise<string> {
	const res = await request<{ path: string }>('/settings/config-yaml-path');
	return res.path;
}
