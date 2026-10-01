import { describe, expect, it } from 'vitest';

import type { Job } from '../src/lib/api';
import { buildClimatologyRun } from '../src/lib/components/metric-map/layerPipeline';
import { deltaLayerKey, rawLayerKey } from '../src/lib/components/metric-map/layerKeys';
import { currentLensKey, type LensSelection } from '../src/lib/components/metric-map/lensSelection';
import type { RunDef } from '../src/lib/components/metric-map/types';

const MODEL: RunDef = { jobId: 'job-a', modelName: 'aifs', colorIndex: 0 };
const CLIMATOLOGY: RunDef = { jobId: 'job-a', modelName: 'climatology', colorIndex: 1 };

const BASELINE: LensSelection = {
	viewMode: 'baseline',
	selectedMetric: 'rmse',
	selectedModelJobId: 'job-a',
	selectedReferenceJobId: 'climatology',
	selectedWindow: 'all',
	selectedReferenceWindow: 'all'
};

const RAW_KEY = rawLayerKey('job-a', 'aifs', 'rmse', 'all');
const DELTA_KEY = deltaLayerKey('job-a', 'aifs', 'rmse', 'all', 'job-a', 'climatology', 'all');

describe('baseline lens', () => {
	it('compares against climatology when the comparison layer exists', () => {
		expect(currentLensKey(BASELINE, [MODEL, CLIMATOLOGY], () => true)).toBe(DELTA_KEY);
	});

	it('shows the model itself when climatology has no data for the metric', () => {
		const layers = new Set([RAW_KEY]);
		expect(currentLensKey(BASELINE, [MODEL, CLIMATOLOGY], (key) => layers.has(key))).toBe(RAW_KEY);
	});

	it('keeps an explicit model-vs-model difference even when it is missing', () => {
		const difference = { ...BASELINE, viewMode: 'difference' as const };
		const other: RunDef = { jobId: 'job-b', modelName: 'gencast', colorIndex: 2 };
		const selection = { ...difference, selectedReferenceJobId: 'job-b' };
		expect(currentLensKey(selection, [MODEL, other, CLIMATOLOGY], () => false)).toBe(
			deltaLayerKey('job-a', 'aifs', 'rmse', 'all', 'job-b', 'gencast', 'all')
		);
	});
});

describe('climatology baseline source', () => {
	const jobs = [{ id: 'prob' }, { id: 'det' }] as Job[];

	it('takes climatology from the first job that wrote it', () => {
		expect(buildClimatologyRun(jobs, (id) => id === 'det')?.jobId).toBe('det');
	});

	it('has no baseline when no job wrote climatology', () => {
		expect(buildClimatologyRun(jobs, () => false)).toBeNull();
	});
});
