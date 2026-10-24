import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { SettingsField } from '../src/lib/api';

function field(name: string, label: string, extra: Partial<SettingsField> = {}): SettingsField {
	return {
		name,
		label,
		description: '',
		type: 'string',
		default: '',
		sensitive: false,
		restart_required: false,
		editable: true,
		multiline: false,
		choices: null,
		...extra
	};
}

const GROUP = {
	name: 'AI assistant',
	fields: [
		field('llm_speculative_decoding', 'Local model speculative decoding', {
			type: 'bool',
			default: true
		}),
		field('llm_draft_model', 'Draft model', { shown_when: 'llm_speculative_decoding' })
	]
};

const api = vi.hoisted(() => ({
	getSettingsSchema: vi.fn(),
	getSettings: vi.fn(),
	getConfigYamlPath: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

import SettingsFields from '../src/lib/components/SettingsFields.svelte';
import { ConfigSettingsState } from '../src/lib/settings/config.svelte';

async function loaded(speculating: boolean): Promise<ConfigSettingsState> {
	api.getSettingsSchema.mockResolvedValue({ deployment_mode: 'personal', groups: [GROUP] });
	api.getSettings.mockResolvedValue({
		values: { llm_speculative_decoding: speculating, llm_draft_model: '' },
		secrets: {}
	});
	api.getConfigYamlPath.mockResolvedValue('');
	const settings = new ConfigSettingsState();
	await settings.load();
	return settings;
}

describe('SettingsFields', () => {
	it('shows the draft model only while speculative decoding is on, saved or not', async () => {
		const settings = await loaded(false);
		render(SettingsFields, { settings, group: GROUP });
		expect(screen.queryByText('Draft model')).toBeNull();

		await fireEvent.click(screen.getByRole('checkbox'));
		expect(screen.getByText('Draft model')).toBeTruthy();

		await fireEvent.click(screen.getByRole('checkbox'));
		expect(screen.queryByText('Draft model')).toBeNull();
	});
});
