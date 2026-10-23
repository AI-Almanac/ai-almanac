import { describe, expect, it } from 'vitest';
import type { ForecastModel } from '../src/lib/api';
import { linkableModels, withForecastModel } from '../src/routes/data-sources/live-links';

const models = [
	{ id: 'aifs2', resolution_deg: 0.25 },
	{ id: 'graphcast', resolution_deg: 1 },
	{ id: 'graphcast_operational', resolution_deg: 0.25 }
] as unknown as ForecastModel[];
const ids = (list: ForecastModel[]) => list.map((model) => model.id);

describe('live forecast links', () => {
	it('offers only models on the archive grid, keeping a saved link listed', () => {
		expect(ids(linkableModels(models, 0.25))).toEqual(['aifs2', 'graphcast_operational']);
		expect(ids(linkableModels(models, 0.25, 'graphcast'))).toEqual([
			'aifs2',
			'graphcast',
			'graphcast_operational'
		]);
		expect(ids(linkableModels(models, undefined))).toEqual(ids(models));
	});

	it('links and unlinks without touching the rest of the metadata', () => {
		const metadata = { model_var: 'tp', forecast_model_id: 'aifs2' };

		expect(withForecastModel(metadata, 'graphcast_operational')).toEqual({
			model_var: 'tp',
			forecast_model_id: 'graphcast_operational'
		});
		expect(withForecastModel(metadata, '')).toEqual({ model_var: 'tp' });
		expect(metadata.forecast_model_id).toBe('aifs2');
	});
});
