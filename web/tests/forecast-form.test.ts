import { describe, expect, it } from 'vitest';

import type { Blend, ForecastModel } from '../src/lib/api';
import {
	canSubmit,
	forecastRequest,
	runnableModels,
	unlinkedModels,
	type ForecastFields
} from '../src/lib/components/forecast-setup/forecast-form';

const blend = {
	id: 'blend-1',
	name: 'Kiremt blend',
	status: 'complete',
	model_names: ['aifs_eth', 'fuxi_eth'],
	forecast_models: { aifs_eth: 'aifs' }
} as unknown as Blend;

const models = [
	{ id: 'aifs', display_name: 'AIFS' },
	{ id: 'fuxi', display_name: 'FuXi' }
] as unknown as ForecastModel[];

const fields: ForecastFields = {
	blendId: 'blend-1',
	modelNames: ['aifs_eth'],
	initSource: 'gfs',
	initTime: '',
	maxIssueDates: undefined
};

describe('the models a forecast can run', () => {
	it('are the blend’s models that are linked to a live forecast model', () => {
		expect(runnableModels(blend, models)).toEqual([{ name: 'aifs_eth', model: models[0] }]);
		expect(unlinkedModels(blend)).toEqual(['fuxi_eth']);
	});

	it('are none before a blend is chosen', () => {
		expect(runnableModels(null, models)).toEqual([]);
		expect(unlinkedModels(null)).toEqual([]);
	});
});

describe('whether a forecast can be submitted', () => {
	const linked = { ...blend, forecast_models: { aifs_eth: 'aifs', fuxi_eth: 'fuxi' } } as Blend;

	it('needs a blend, a model, and every model of the blend linked', () => {
		expect(canSubmit(fields, linked)).toBe(true);
		expect(canSubmit(fields, blend)).toBe(false);
		expect(canSubmit({ ...fields, modelNames: [] }, linked)).toBe(false);
		expect(canSubmit({ ...fields, blendId: '' }, null)).toBe(false);
	});
});

describe('the request a forecast is submitted with', () => {
	it('leaves out the settings left at their defaults', () => {
		expect(forecastRequest({ ...fields, initSource: '' })).toEqual({
			blend_id: 'blend-1',
			forecast_model_ids: ['aifs_eth']
		});
	});

	it('carries the settings that were given, trimmed', () => {
		expect(
			forecastRequest({ ...fields, initTime: ' 2027-05-01T00 ', maxIssueDates: 3 }).params
		).toEqual({ init_time: '2027-05-01T00', init_source: 'gfs', max_issue_dates: 3 });
	});
});
