import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Blend, BlendRunSpec, DataSource } from '../src/lib/api';

const api = vi.hoisted(() => ({ createBlend: vi.fn() }));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, ...api };
});

vi.mock('../src/lib/components/FocusAreaMap.svelte', async () => ({
	default: (await import('./fixtures/StubComponent.svelte')).default
}));

import BlendForm from '../src/lib/components/blend-setup/BlendForm.svelte';
import {
	blankBlendFields,
	blendRequest,
	fieldsFromSpec,
	withObservations
} from '../src/lib/components/blend-setup/blend-form';

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
	})
];
const models = [
	source({ id: 'aifs', name: 'AIFS' }),
	source({ id: 'graphcast', name: 'GraphCast' })
];

function spec(overrides: Partial<BlendRunSpec>): BlendRunSpec {
	return {
		name: 'Assistant blend',
		obs_dataset_id: 'chirps',
		model_ids: ['aifs', 'graphcast'],
		...overrides
	} as BlendRunSpec;
}

function renderForm(startFrom: BlendRunSpec | null = null) {
	return render(BlendForm, {
		obsSources: observations,
		modelSources: models,
		dayLevelAvailable: true,
		minTrainingYears: 5,
		startFrom,
		onSubmitted: vi.fn(),
		onCancel: vi.fn()
	});
}

function field(placeholder: string): HTMLInputElement {
	return screen.getAllByPlaceholderText(placeholder)[0] as HTMLInputElement;
}

const threshold = () => field('20');
const trees = () => field('100');
const trainingYears = () => field('2015:2020');

describe('a blend setup handed to the form', () => {
	it('replaces the day-level settings typed before it', async () => {
		const { rerender } = renderForm(spec({ name: 'First' }));
		await fireEvent.input(trees(), { target: { value: '50' } });

		await rerender({ startFrom: spec({ name: 'Second' }) });

		await waitFor(() => expect(field('e.g. India monsoon blend').value).toBe('Second'));
		expect(trees().value).toBe('');
	});

	it('gets a suggested year split when it names no years, even after one that did', async () => {
		const { rerender } = renderForm(
			spec({ name: 'First', training_years: '2001:2003', cv_holdout_years: '2001:2003' })
		);
		await waitFor(() => expect(trainingYears().value).toBe('2001:2003'));

		await rerender({ startFrom: spec({ name: 'Second' }) });

		await waitFor(() => expect(trainingYears().value).not.toBe('2001:2003'));
		expect(trainingYears().value).not.toBe('');
	});
});

describe('training a blend', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		api.createBlend.mockResolvedValue({ id: 'blend-1', name: 'Kiremt blend' } as Blend);
	});

	it('leaves no onset override behind for the next blend', async () => {
		renderForm(spec({ name: 'Kiremt blend' }));
		await fireEvent.input(threshold(), { target: { value: '30' } });
		const train = screen.getByRole('button', { name: 'Train blend' }) as HTMLButtonElement;
		await waitFor(() => expect(train.disabled).toBe(false));

		await fireEvent.click(train);

		await waitFor(() => expect(api.createBlend).toHaveBeenCalledOnce());
		expect(api.createBlend.mock.calls[0][0].params.threshold_mm).toBe(30);
		await waitFor(() => expect(threshold().value).toBe(''));
	});

	it('shows the server’s explanation when training is refused', async () => {
		const { ApiError } = await import('../src/lib/api/core');
		api.createBlend.mockRejectedValue(
			new ApiError(422, 'No forecast years remain.', 'POST /blends failed: 422 {"detail":"…"}')
		);
		renderForm(spec({ name: 'Kiremt blend' }));
		const train = screen.getByRole('button', { name: 'Train blend' }) as HTMLButtonElement;
		await waitFor(() => expect(train.disabled).toBe(false));

		await fireEvent.click(train);

		expect(await screen.findByText('No forecast years remain.')).toBeTruthy();
	});
});

describe('choosing other observations', () => {
	const india = source({
		id: 'imd',
		kind: 'obs',
		metadata: { spatial_bounds: { lat_min: 8, lat_max: 30, lon_min: 68, lon_max: 97 } }
	});
	const box = { lat_min: 5, lat_max: 10, lon_min: 36, lon_max: 40 };

	it('drops a drawn box that misses their data', () => {
		const fields = { ...blankBlendFields(), focusArea: box };
		expect(withObservations(fields, india)).toMatchObject({ obsDatasetId: 'imd', focusArea: null });
	});

	it('keeps picked places, and a box where their extent is unknown', () => {
		const places = { level: 'adm1' as const, units: ['Amhara'], iso: 'ETH' };
		const withPlaces = withObservations({ ...blankBlendFields(), focusArea: places }, india);
		const withBox = withObservations({ ...blankBlendFields(), focusArea: box }, observations[0]);
		expect(withPlaces.focusArea).toBe(places);
		expect(withBox.focusArea).toBe(box);
	});
});

describe('the request a blend form sends', () => {
	it('leaves out every setting left blank, and day-level settings the run does not offer', () => {
		const fields = {
			...fieldsFromSpec(spec({ training_years: '2010:2018', cv_holdout_years: '2010:2018' })),
			dayLevel: { ...blankBlendFields().dayLevel, trees: '50' }
		};
		expect(blendRequest(fields, false)).toEqual({
			name: 'Assistant blend',
			obs_dataset_id: 'chirps',
			model_ids: ['aifs', 'graphcast'],
			params: { training_years: '2010:2018', cv_holdout_years: '2010:2018' }
		});
		expect(blendRequest(fields, true).params.day_level).toEqual({ trees: 50 });
	});
});
