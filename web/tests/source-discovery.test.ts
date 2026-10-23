import { describe, expect, it } from 'vitest';
import type { DiscoveredSource } from '../src/lib/api';
import {
	canRegister,
	initialChoices,
	initialSelection,
	proposalSummary,
	registrationBody
} from '../src/routes/data-sources/discovery';

function proposal(path: string, overrides: Partial<DiscoveredSource> = {}): DiscoveredSource {
	return {
		kind: 'obs',
		name: `${path} obs`,
		path,
		region: 'india',
		metadata: { obs_file_pattern: 'data_{}.nc', obs_var: 'RAINFALL' },
		status: 'ready',
		validation_error: null,
		registered_as: null,
		...overrides
	};
}

describe('source discovery', () => {
	it('ticks only proposals that validated, are new, and already have a region', () => {
		const sources = [
			proposal('/d/ready'),
			proposal('/d/no-region', { region: null }),
			proposal('/d/invalid', { status: 'invalid', validation_error: 'bad' }),
			proposal('/d/taken', { registered_as: 'India obs' })
		];

		expect(initialSelection(sources)).toEqual({
			'/d/ready': true,
			'/d/no-region': false,
			'/d/invalid': false,
			'/d/taken': false
		});
		expect(sources.map(canRegister)).toEqual([true, true, false, false]);
	});

	it('registers the validated metadata under the name and region the user chose', () => {
		const source = proposal('/d/obs', { region: null });
		const [choice] = Object.values(initialChoices([source]));

		expect(choice).toEqual({ name: '/d/obs obs', region: '', forecastModelId: '' });
		expect(
			registrationBody(source, { name: '  India gauges ', region: 'india', forecastModelId: '' })
		).toEqual({
			kind: 'obs',
			name: 'India gauges',
			path: '/d/obs',
			region: 'india',
			metadata: { obs_file_pattern: 'data_{}.nc', obs_var: 'RAINFALL' }
		});
		expect(
			registrationBody(source, { name: '   ', region: 'india', forecastModelId: '' }).name
		).toBe('/d/obs obs');
	});

	it('starts from the suggested live model and registers whichever the user leaves chosen', () => {
		const model = proposal('/d/aifs', {
			kind: 'model',
			metadata: { model_var: 'tp', grid_step_deg: 0.25, forecast_model_id: 'aifs2' }
		});
		const [choice] = Object.values(initialChoices([model]));

		expect(choice.forecastModelId).toBe('aifs2');
		expect(registrationBody(model, choice).metadata?.forecast_model_id).toBe('aifs2');
		expect(registrationBody(model, { ...choice, forecastModelId: '' }).metadata).not.toHaveProperty(
			'forecast_model_id'
		);
	});

	it('summarises the files, variable, years, and ensemble flag found', () => {
		const obs = proposal('/d/obs', {
			metadata: {
				obs_file_pattern: 'data_{}.nc',
				obs_var: 'RAINFALL',
				start_year: 1901,
				end_year: 2025
			}
		});
		const ensemble = proposal('/d/aifs', {
			kind: 'model',
			metadata: { file_pattern: '{}.nc', model_var: 'tp', probabilistic: true }
		});

		expect(proposalSummary(obs)).toBe('data_{}.nc · RAINFALL · 1901–2025');
		expect(proposalSummary(ensemble)).toBe('{}.nc · tp · ensemble');
	});
});
