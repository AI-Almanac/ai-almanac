import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Blend, BlendRunSpec, DataSource } from '../src/lib/api';

const api = vi.hoisted(() => ({
	listDataSources: vi.fn(),
	getCapabilities: vi.fn(),
	getGuardrailThresholds: vi.fn(),
	createBlend: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

vi.mock('../src/lib/components/FocusAreaMap.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

import BlendForm from '../src/lib/components/blend-setup/BlendForm.svelte';
import NewBlendPanel from '../src/lib/components/workspace/NewBlendPanel.svelte';

function source(overrides: Partial<DataSource>): DataSource {
	return {
		id: 'source',
		name: 'Source',
		kind: 'model',
		region: 'ethiopia',
		status: 'ready',
		metadata: { start_year: 2000, end_year: 2022 },
		...overrides
	} as DataSource;
}

const observations = [
	source({
		id: 'chirps',
		name: 'CHIRPS',
		kind: 'obs',
		metadata: { start_year: 1990, end_year: 2022 }
	}),
	source({ id: 'draft-obs', name: 'Unfinished rainfall', kind: 'obs', status: 'invalid' })
];
const models = [
	source({ id: 'aifs', name: 'AIFS' }),
	source({ id: 'graphcast', name: 'GraphCast' }),
	source({ id: 'india-model', name: 'India only', region: 'india' })
];

function renderPanel(onStarted = vi.fn().mockResolvedValue(undefined)) {
	const onCancel = vi.fn();
	render(NewBlendPanel, { onStarted, onCancel });
	return { onStarted, onCancel };
}

async function chooseObservations(name: string) {
	const option = (await screen.findByRole('option', { name })) as HTMLOptionElement;
	await fireEvent.change(screen.getByRole('combobox'), { target: { value: option.value } });
}

async function fillValidBlend() {
	await fireEvent.input(await screen.findByPlaceholderText('e.g. India monsoon blend'), {
		target: { value: 'Kiremt blend' }
	});
	await chooseObservations('CHIRPS (ethiopia)');
	await fireEvent.click(screen.getByRole('checkbox', { name: /AIFS/ }));
	await fireEvent.click(screen.getByRole('checkbox', { name: /GraphCast/ }));
}

describe('starting a blend from the workspace', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		api.listDataSources.mockImplementation(async (kind: 'obs' | 'model') =>
			kind === 'obs' ? observations : models
		);
		api.getCapabilities.mockResolvedValue({ chat: true, day_level_blend: false });
		api.getGuardrailThresholds.mockResolvedValue({ min_training_years: 5 });
		api.createBlend.mockResolvedValue({ id: 'blend-1', name: 'Kiremt blend' } as Blend);
	});

	it('offers the ready observation datasets, and the models that match the chosen one', async () => {
		renderPanel();

		expect(screen.getByRole('heading', { name: 'New blend' })).toBeTruthy();
		await chooseObservations('CHIRPS (ethiopia)');

		expect(screen.queryByRole('option', { name: /Unfinished rainfall/ })).toBeNull();
		expect(screen.getByRole('checkbox', { name: /AIFS/ })).toBeTruthy();
		expect(screen.getByRole('checkbox', { name: /GraphCast/ })).toBeTruthy();
		expect(screen.queryByRole('checkbox', { name: /India only/ })).toBeNull();
	});

	it('opens filled in with a setup it was given, and says who filled it in', async () => {
		render(NewBlendPanel, {
			draft: { name: 'Kiremt blend', obs_dataset_id: 'chirps', model_ids: ['aifs'] },
			onStarted: vi.fn(),
			onCancel: vi.fn()
		});

		const name = (await screen.findByPlaceholderText(
			'e.g. India monsoon blend'
		)) as HTMLInputElement;

		expect(name.value).toBe('Kiremt blend');
		expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('chirps');
		expect((screen.getByRole('checkbox', { name: /AIFS/ }) as HTMLInputElement).checked).toBe(true);
		expect(screen.getByText(/Filled in by the assistant/)).toBeTruthy();
	});

	it('leaves without starting anything when cancelled', async () => {
		const { onStarted, onCancel } = renderPanel();

		await fireEvent.click((await screen.findAllByRole('button', { name: 'Cancel' }))[0]);

		expect(onCancel).toHaveBeenCalledOnce();
		expect(onStarted).not.toHaveBeenCalled();
		expect(api.createBlend).not.toHaveBeenCalled();
	});

	it('trains the blend that was set up and hands it to the workspace', async () => {
		const { onStarted } = renderPanel();
		const train = (await screen.findByRole('button', { name: 'Train blend' })) as HTMLButtonElement;
		expect(train.disabled).toBe(true);

		await fillValidBlend();
		await waitFor(() => expect(train.disabled).toBe(false));
		await fireEvent.click(train);

		await waitFor(() => expect(onStarted).toHaveBeenCalledWith(['blend-1']));
		expect(api.createBlend).toHaveBeenCalledWith(
			expect.objectContaining({
				name: 'Kiremt blend',
				obs_dataset_id: 'chirps',
				model_ids: ['aifs', 'graphcast']
			})
		);
	});

	it('says how to add the blend when it started but could not join the workspace', async () => {
		renderPanel(vi.fn().mockRejectedValue(new Error('offline')));

		await fillValidBlend();
		const train = screen.getByRole('button', { name: 'Train blend' }) as HTMLButtonElement;
		await waitFor(() => expect(train.disabled).toBe(false));
		await fireEvent.click(train);

		const alert = await screen.findByRole('alert');
		expect(alert.textContent).toBe(
			'The blend started, but it could not be added to this workspace. Add it with New → Add an existing run.'
		);
	});
});

describe('a blend setup handed to the form', () => {
	it('fills the fields, ready to train', async () => {
		render(BlendForm, {
			obsSources: [observations[0]],
			modelSources: models,
			startFrom: {
				name: 'Assistant blend',
				obs_dataset_id: 'chirps',
				model_ids: ['aifs', 'graphcast'],
				training_years: '2010:2018',
				cv_holdout_years: '2010:2018'
			} as BlendRunSpec,
			onSubmitted: vi.fn(),
			onCancel: vi.fn()
		});

		const name = screen.getByPlaceholderText('e.g. India monsoon blend') as HTMLInputElement;
		await waitFor(() => expect(name.value).toBe('Assistant blend'));
		expect((screen.getByRole('checkbox', { name: /AIFS/ }) as HTMLInputElement).checked).toBe(true);
		expect((screen.getAllByPlaceholderText('2015:2020')[0] as HTMLInputElement).value).toBe(
			'2010:2018'
		);
		const train = screen.getByRole('button', { name: 'Train blend' }) as HTMLButtonElement;
		expect(train.disabled).toBe(false);
	});
});
