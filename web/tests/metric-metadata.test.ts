import { describe, expect, it } from 'vitest';
import type { MetricDefinition } from '../src/lib/api';
import {
	formatMetricDelta,
	formatMetricValue,
	lowerIsBetter,
	metricMap,
	metricOptions,
	metricUnit,
	windowLabel
} from '../src/lib/metric-metadata';

const definitions: MetricDefinition[] = [
	{
		id: 'false_alarm_rate',
		label: 'False alarm rate',
		abbreviation: 'FAR',
		unit: 'fraction',
		lower_is_better: true,
		description: ''
	},
	{
		id: 'mean_mae',
		label: 'Mean absolute error',
		abbreviation: 'MAE',
		unit: 'days',
		lower_is_better: true,
		description: ''
	},
	{
		id: 'mae',
		label: 'Mean absolute error',
		abbreviation: 'MAE',
		unit: 'mm',
		lower_is_better: true,
		description: ''
	},
	{
		id: 'acc',
		label: 'Anomaly correlation coefficient',
		abbreviation: 'ACC',
		unit: 'dimensionless',
		lower_is_better: false,
		description: ''
	},
	{
		id: 'bias',
		label: 'Mean bias',
		abbreviation: 'Bias',
		unit: 'mm',
		lower_is_better: false,
		description: ''
	}
];

describe('metric metadata helpers', () => {
	it('formats known metric units', () => {
		expect(formatMetricValue(0.1234, 'fraction')).toBe('12.3%');
		expect(formatMetricValue(2.25, 'days')).toBe('2.3 d');
		expect(formatMetricValue(4.567, 'mm')).toBe('4.57 mm');
		expect(formatMetricValue(0.8766, 'dimensionless')).toBe('0.877');
		expect(formatMetricDelta(-1.5, 'mm')).toBe('-1.50 mm');
	});

	it('orders configured metrics before annual onset series', () => {
		const options = metricOptions(['mae_2020', 'acc', 'false_alarm_rate', 'mae'], definitions);
		expect(options.map((option) => option.value)).toEqual(['false_alarm_rate', 'mae', 'acc']);
	});

	it('uses config units before API fallbacks and treats bias deltas as neutral', () => {
		const byId = metricMap(definitions);
		expect(metricUnit('mae', 'days', byId)).toBe('mm');
		expect(lowerIsBetter('acc', byId)).toBe(false);
		expect(lowerIsBetter('bias', byId)).toBeNull();
	});

	it('labels the all verification window', () => {
		expect(windowLabel('all')).toBe('All days');
		expect(windowLabel('1-15')).toBe('Days 1–15');
	});
});
