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
	name: 'Model',
	fields: [
		field('llm_engine', 'Model server', { choices: ['external', 'managed'] }),
		field('llm_model', 'Model', {
			presets: [
				{ value: 'nvidia/Qwen3.8-27B-NVFP4', label: 'Qwen3.8 27B' },
				{ value: 'nvidia/Nemotron', label: 'Nemotron' }
			],
			presets_when: { field: 'llm_engine', one_of: ['managed'] }
		})
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

async function loaded(engine: string, model: string): Promise<ConfigSettingsState> {
	api.getSettingsSchema.mockResolvedValue({ deployment_mode: 'personal', groups: [GROUP] });
	api.getSettings.mockResolvedValue({
		values: { llm_engine: engine, llm_model: model },
		secrets: {}
	});
	api.getConfigYamlPath.mockResolvedValue('');
	const settings = new ConfigSettingsState();
	await settings.load();
	return settings;
}

const modelSelect = () => screen.getByRole('combobox', { name: 'Model' }) as HTMLSelectElement;
const customInput = () => screen.queryByRole('textbox', { name: 'Model (custom)' });

describe('SettingsFields presets', () => {
	it('picks a catalog model from the dropdown', async () => {
		const settings = await loaded('managed', 'nvidia/Qwen3.8-27B-NVFP4');
		render(SettingsFields, { settings, group: GROUP });
		expect(modelSelect().value).toBe('nvidia/Qwen3.8-27B-NVFP4');
		expect(customInput()).toBeNull();

		await fireEvent.change(modelSelect(), { target: { value: 'nvidia/Nemotron' } });
		expect(settings.value('llm_model')).toBe('nvidia/Nemotron');
	});

	it('edits any other model in the custom box', async () => {
		const settings = await loaded('managed', '/models/my-finetune');
		render(SettingsFields, { settings, group: GROUP });
		expect(modelSelect().value).toBe('__custom__');
		expect((customInput() as HTMLInputElement).value).toBe('/models/my-finetune');

		await fireEvent.change(modelSelect(), { target: { value: 'nvidia/Nemotron' } });
		expect(customInput()).toBeNull();
		await fireEvent.change(modelSelect(), { target: { value: '__custom__' } });
		// Switching to custom keeps the value, ready to edit.
		expect((customInput() as HTMLInputElement).value).toBe('nvidia/Nemotron');
		await fireEvent.input(customInput() as HTMLInputElement, {
			target: { value: 'nvidia/Nemotron-v2' }
		});
		expect(settings.value('llm_model')).toBe('nvidia/Nemotron-v2');
		expect(modelSelect().value).toBe('__custom__');
	});

	it('offers only the text box for an external server', async () => {
		const settings = await loaded('external', 'gpt-oss');
		render(SettingsFields, { settings, group: GROUP });
		expect(screen.queryByRole('combobox', { name: 'Model' })).toBeNull();
		expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('gpt-oss');
	});
});
