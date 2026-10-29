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
	/** Present for enum-valued fields; the form renders a select. */
	choices: string[] | null;
	/** Common values for a free-text field; the input offers them while typing. */
	suggestions?: string[] | null;
	/** Conditions that must all hold (in the form, saved or not) for this one to show. */
	shown_when?: FieldCondition[] | null;
	/** The subheading under the group's Advanced section; null shows it above. */
	advanced_section?: string | null;
	/** Labelled values offered in a select, beside a custom-value input. */
	presets?: { value: string; label: string }[] | null;
	/** Offer the presets only while this holds. */
	presets_when?: FieldCondition | null;
}

/** Holds while `field` (as edited) has one of `one_of`. */
export interface FieldCondition {
	field: string;
	one_of: (string | boolean)[];
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
