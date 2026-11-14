import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Dataset, Job, ModelConfig, Region } from '../src/lib/api';
import type { BenchmarkStore } from '../src/lib/benchmarks.svelte';

const api = vi.hoisted(() => ({ getModelsScoredWith: vi.fn() }));

vi.mock('../src/lib/api', async () => {
	const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
	return { ...actual, getModelsScoredWith: api.getModelsScoredWith };
});

import { BenchmarkSetupForm } from '../src/lib/components/benchmark-setup/setup-form.svelte';
import {
	benchmarkStart,
	benchmarkStartFromDraft,
	blendSetupFromDraft,
	latestBenchmarkJobIds
} from '../src/lib/workspace/setup-start';
import { workspaceJob } from './fixtures/workspace';

function job(overrides: Partial<Job>): Job {
	return {
		id: 'job-1',
		status: 'complete',
		dataset_id: 'chirps',
		model_name: 'aifs',
		model_display_name: 'AIFS',
		region_id: 'ethiopia',
		...overrides
	};
}

describe('what a new benchmark starts from', () => {
	it('takes the jobs of the newest benchmark, passing over newer blends', () => {
		const jobs = [
			workspaceJob({ id: 'old', run_id: 'run-old', created_at: '2026-09-01T00:00:00Z' }),
			workspaceJob({ id: 'new-a', run_id: 'run-new', created_at: '2026-09-20T00:00:00Z' }),
			workspaceJob({ id: 'new-b', run_id: 'run-new', created_at: '2026-09-20T00:00:00Z' }),
			workspaceJob({
				id: 'blend',
				run_id: null,
				job_type: 'blend',
				created_at: '2026-09-25T00:00:00Z'
			})
		];

		expect(latestBenchmarkJobIds(jobs).sort()).toEqual(['new-a', 'new-b']);
		expect(latestBenchmarkJobIds([jobs[3]])).toEqual([]);
	});

	it('carries the region, observations, models, area of interest and onset definition', () => {
		const area = { units: ['Amhara'], level: 'adm1' } as unknown as NonNullable<
			NonNullable<Job['params']>['focus_area']
		>;
		const params = {
			region: 'ethiopia',
			focus_area: area,
			wet_threshold: 20,
			wet_spell: 3,
			start_date: '1998-05-01'
		};

		const start = benchmarkStart([
			job({ params }),
			job({ id: 'job-2', model_name: 'fuxi', params })
		]);

		expect(start).toEqual({
			regionId: 'ethiopia',
			datasetId: 'chirps',
			modelIds: ['aifs', 'fuxi'],
			sharedParams: { focus_area: area, wet_threshold: 20, wet_spell: 3 }
		});
	});

	it('names each model by the forecast data the form lists, not by what the scoring run called it', () => {
		const start = benchmarkStart([
			job({ model_name: 'aifs_ens', model_source_id: 'src-aifs' }),
			job({ id: 'job-2', model_name: 'fuxi_s2s', model_source_id: 'src-fuxi', status: 'running' })
		]);

		expect(start?.modelIds).toEqual(['src-aifs', 'src-fuxi']);
	});

	it('starts blank when there is no earlier benchmark to read', () => {
		expect(benchmarkStart([])).toBeNull();
	});
});

describe('what a form starts from when it is given a draft', () => {
	it('takes a benchmark’s region from its observations when the draft names none', () => {
		const start = benchmarkStartFromDraft({ dataset_id: 'chirps', model_ids: ['aifs'] }, [
			{ id: 'chirps', region: 'ethiopia' }
		]);

		expect(start).toEqual({
			regionId: 'ethiopia',
			datasetId: 'chirps',
			modelIds: ['aifs'],
			sharedParams: {}
		});
	});

	it('leaves a blend’s unsettled fields blank', () => {
		const setup = blendSetupFromDraft({ name: 'Kiremt blend', model_ids: ['aifs'] });

		expect(setup).toMatchObject({
			name: 'Kiremt blend',
			model_ids: ['aifs'],
			obs_dataset_id: null,
			training_years: '',
			focus_area: null
		});
	});
});

describe('a benchmark form started from an earlier setup', () => {
	const model = (id: string, grid: number): ModelConfig => ({
		id,
		display_name: id,
		region: 'ethiopia',
		model_type: 'graph',
		model_dir: `/models/${id}`,
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
		grid_step_deg: grid
	});

	function form(): BenchmarkSetupForm {
		const setup = new BenchmarkSetupForm(
			() => ({}) as BenchmarkStore,
			() => {}
		);
		setup.regions = [{ id: 'ethiopia', display_name: 'Ethiopia' } as Region];
		setup.datasets = [{ id: 'chirps', name: 'CHIRPS', grid_step_deg: 0.25 } as Dataset];
		return setup;
	}

	beforeEach(() => api.getModelsScoredWith.mockReset());

	it('is ready to run with the earlier choices, less a model that no longer fits', async () => {
		api.getModelsScoredWith.mockResolvedValue([model('aifs', 0.25), model('fuxi', 1)]);
		const setup = form();

		await setup.startFrom({
			regionId: 'ethiopia',
			datasetId: 'chirps',
			modelIds: ['aifs', 'fuxi'],
			sharedParams: { wet_threshold: 20 }
		});

		expect(setup.selectedRegionId).toBe('ethiopia');
		expect(setup.selectedDatasetId).toBe('chirps');
		expect(setup.selectedModelIds).toEqual(['aifs']);
		expect(setup.sharedAdvancedParams).toEqual({ wet_threshold: 20 });
		expect(setup.canRun).toBe(true);
	});

	it('selects the models of a benchmark that is still running', async () => {
		api.getModelsScoredWith.mockResolvedValue([model('src-aifs', 0.25), model('src-fuxi', 0.25)]);
		const setup = form();
		const start = benchmarkStart([
			job({ model_name: 'aifs_ens', model_source_id: 'src-aifs', status: 'running' }),
			job({ id: 'job-2', model_name: 'fuxi_s2s', model_source_id: 'src-fuxi', status: 'queued' })
		]);

		await setup.startFrom(start!);

		expect(setup.selectedModels.map((chosen) => chosen.id)).toEqual(['src-aifs', 'src-fuxi']);
		expect(setup.canRun).toBe(true);
	});

	it('leaves observations that are no longer registered to be chosen again', async () => {
		api.getModelsScoredWith.mockResolvedValue([model('aifs', 0.25)]);
		const setup = form();

		await setup.startFrom({
			regionId: 'ethiopia',
			datasetId: 'gone',
			modelIds: ['aifs'],
			sharedParams: {}
		});

		expect(setup.selectedDatasetId).toBe('');
		expect(setup.canRun).toBe(false);
	});
});
