import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Dataset, ModelConfig } from '../src/lib/api';
import type { BenchmarkStore } from '../src/lib/benchmarks.svelte';

const api = vi.hoisted(() => ({
	getModels: vi.fn()
}));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return {
		...actual,
		getModels: api.getModels
	};
});

import { BenchmarkSetupForm } from '../src/routes/benchmarks/setup-form.svelte';

function modelConfig(overrides: Partial<ModelConfig> = {}): ModelConfig {
	return {
		id: 'fuxi',
		display_name: 'FuXi',
		region: 'india',
		model_type: 'graph',
		model_dir: '/models/fuxi',
		model_var: 'tp',
		unit_cvt: null,
		file_pattern: '{}.nc',
		probabilistic: false,
		members: null,
		init_days: '0,3',
		start_date: '2015-05-01',
		end_date: '2024-09-30',
		start_year_clim: 2015,
		end_year_clim: 2024,
		...overrides
	};
}

function dataset(overrides: Partial<Dataset> = {}): Dataset {
	return {
		id: 'obs-india',
		name: 'IMD Rainfall',
		status: 'ready',
		region: 'india',
		is_demo: false,
		created_at: '2026-06-01T00:00:00Z',
		obs_year_start: 2018,
		obs_year_end: 2022,
		...overrides
	};
}

function setupForm(): BenchmarkSetupForm {
	const store = {} as BenchmarkStore;
	const form = new BenchmarkSetupForm(
		() => store,
		() => {}
	);
	form.datasets = [dataset()];
	form.regions = [
		{
			id: 'india',
			display_name: 'India',
			romp_region: 'India',
			description: '',
			has_data: true,
			source_count: 1,
			lat_min: 6,
			lat_max: 36,
			lon_min: 68,
			lon_max: 98,
			land_only: true,
			shp_only: false,
			is_builtin: true,
			boundary_iso: 'IND'
		}
	];
	return form;
}

describe('BenchmarkSetupForm', () => {
	beforeEach(() => {
		api.getModels.mockReset();
	});

	it('switches region and loads its models when the dataset belongs elsewhere', async () => {
		api.getModels.mockResolvedValue([modelConfig()]);
		const form = setupForm();

		form.setDatasetId('obs-india');
		await vi.waitFor(() => expect(form.models).toHaveLength(1));

		expect(form.selectedRegionId).toBe('india');
		expect(form.selectedModelIds).toEqual([]);
		expect(api.getModels).toHaveBeenCalledWith('india');
	});

	it('seeds model overrides clamped to the dataset coverage', async () => {
		api.getModels.mockResolvedValue([modelConfig()]);
		const form = setupForm();
		form.setDatasetId('obs-india');
		await vi.waitFor(() => expect(form.models).toHaveLength(1));

		form.toggleModel('fuxi');

		expect(form.selectedModelIds).toEqual(['fuxi']);
		const override = form.perModelOverrides.fuxi;
		expect(override.start_date).toBe('2018-05-01');
		expect(override.end_date).toBe('2022-09-30');
		expect(override.start_year_clim).toBe(2018);
		expect(override.end_year_clim).toBe(2022);
	});

	it('applies a chat-produced spec to the selection state', () => {
		api.getModels.mockResolvedValue([modelConfig()]);
		const form = setupForm();

		form.applySpec({
			intent: 'compare onset skill',
			status: 'runnable',
			region_id: 'india',
			region_name: 'India',
			romp_region: 'India',
			event_type: 'monsoon_onset',
			dataset_id: 'obs-india',
			dataset_name: 'IMD Rainfall',
			model_ids: ['fuxi'],
			model_names: ['FuXi'],
			forecast_window_days: 45,
			advanced_params: { wet_threshold: 5, per_model_params: { fuxi: { init_days: '0' } } },
			missing_fields: [],
			assumptions: []
		});

		expect(form.selectedRegionId).toBe('india');
		expect(form.selectedDatasetId).toBe('obs-india');
		expect(form.selectedModelIds).toEqual(['fuxi']);
		expect(form.forecastWindowDays).toBe(45);
		expect(form.sharedAdvancedParams).toEqual({ wet_threshold: 5 });
		expect(form.perModelOverrides).toEqual({ fuxi: { init_days: '0' } });
		expect(api.getModels).toHaveBeenCalledWith('india');
	});
});
