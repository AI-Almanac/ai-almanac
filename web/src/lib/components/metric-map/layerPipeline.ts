import type { Job, JobGridResponse } from '$lib/api';
import { DIVERGING_STOPS, sharedStops } from './constants';
import {
	boundsFromGrid,
	buildDeltaGeojson,
	buildRawGeojson,
	buildSharedRawGeojson
} from './gridData';
import { deltaLayerKey, mapLayerId, mapSourceId, rawLayerKey } from './layerKeys';
import type { LayerState, MetricDef, RunDef, WindowDef } from './types';
import { sameRun } from './lensSelection';

export type FetchResult =
	| { run: RunDef; windowValue: string; metricValue: string; data: JobGridResponse }
	| { run: RunDef; windowValue: string; metricValue: string; error: string };

export type LayerEntry = {
	key: string;
	state: LayerState;
};

export function buildModelRuns(jobs: Job[]): RunDef[] {
	return jobs.map((job, i) => ({
		jobId: job.id,
		modelName: job.model_name,
		colorIndex: i
	}));
}

export function buildClimatologyRun(jobs: Job[]): RunDef | null {
	if (jobs.length === 0) return null;
	return {
		jobId: jobs[0].id,
		modelName: 'climatology',
		colorIndex: jobs.length
	};
}

export function allRawLayerKeys(
	fetchRuns: RunDef[],
	activeWindows: WindowDef[],
	metrics: MetricDef[]
) {
	return fetchRuns.flatMap((run) =>
		activeWindows.flatMap((window) =>
			metrics.map((metric) => rawLayerKey(run.jobId, run.modelName, metric.value, window.value))
		)
	);
}

export async function fetchGridResults(
	fetchRuns: RunDef[],
	activeWindows: WindowDef[],
	metrics: MetricDef[],
	getGrid: (
		jobId: string,
		modelName: string,
		windowValue: string,
		metricValue: string
	) => Promise<JobGridResponse>
): Promise<FetchResult[]> {
	return Promise.all(
		fetchRuns.flatMap((run) =>
			activeWindows.flatMap((window) =>
				metrics.map(async (metric) => {
					try {
						const data = await getGrid(run.jobId, run.modelName, window.value, metric.value);
						return { run, windowValue: window.value, metricValue: metric.value, data };
					} catch (e) {
						return {
							run,
							windowValue: window.value,
							metricValue: metric.value,
							error: e instanceof Error ? e.message : 'Failed to load'
						};
					}
				})
			)
		)
	);
}

export function indexGridResults(results: FetchResult[]) {
	const dataByRunMetric: Record<string, JobGridResponse> = {};
	let hasClimatology = false;

	for (const result of results) {
		if (!('data' in result)) continue;
		dataByRunMetric[
			rawLayerKey(result.run.jobId, result.run.modelName, result.metricValue, result.windowValue)
		] = result.data;
		if (result.run.modelName === 'climatology') {
			hasClimatology = true;
		}
	}

	return { dataByRunMetric, hasClimatology };
}

export function computeSharedRanges(
	activeRuns: RunDef[],
	activeWindows: WindowDef[],
	metrics: MetricDef[],
	dataByRunMetric: Record<string, JobGridResponse>
) {
	const sharedRangeByMetric: Record<string, { min: number; max: number }> = {};
	for (const metric of metrics) {
		const values = activeRuns
			.flatMap((run) =>
				activeWindows.map(
					(window) =>
						dataByRunMetric[rawLayerKey(run.jobId, run.modelName, metric.value, window.value)]
				)
			)
			.filter((data): data is JobGridResponse => Boolean(data));
		if (values.length > 0) {
			sharedRangeByMetric[metric.value] = {
				min: Math.min(...values.map((data) => data.min)),
				max: Math.max(...values.map((data) => data.max))
			};
		}
	}
	return sharedRangeByMetric;
}

export function buildRawLayerEntries(
	results: FetchResult[],
	sharedRangeByMetric: Record<string, { min: number; max: number }>
) {
	const entries: LayerEntry[] = [];
	const errors: Record<string, string> = {};

	for (const result of results) {
		const key = rawLayerKey(
			result.run.jobId,
			result.run.modelName,
			result.metricValue,
			result.windowValue
		);
		if ('error' in result) {
			if (result.run.modelName !== 'climatology') errors[key] = result.error;
			continue;
		}

		const { metricValue, data } = result;
		const sharedRange = sharedRangeByMetric[metricValue];
		const stops = sharedStops(metricValue);
		const geojson = sharedRange
			? buildSharedRawGeojson(data, stops, sharedRange.min, sharedRange.max)
			: buildRawGeojson(data, stops);
		const displayData = sharedRange
			? { ...data, min: sharedRange.min, max: sharedRange.max }
			: data;

		entries.push({
			key,
			state: {
				layerId: mapLayerId(key),
				sourceId: mapSourceId(key),
				data: displayData,
				geojson,
				bounds: boundsFromGrid(data),
				stops,
				isDelta: false
			}
		});
	}

	return { entries, errors };
}

export function buildDeltaLayerEntries(
	modelRuns: RunDef[],
	activeRuns: RunDef[],
	activeWindows: WindowDef[],
	metrics: MetricDef[],
	dataByRunMetric: Record<string, JobGridResponse>
) {
	const entries: LayerEntry[] = [];

	for (const run of modelRuns) {
		for (const metric of metrics) {
			for (const window of activeWindows) {
				const modelData =
					dataByRunMetric[rawLayerKey(run.jobId, run.modelName, metric.value, window.value)];
				if (!modelData) continue;
				for (const reference of activeRuns) {
					for (const referenceWindow of activeWindows) {
						if (sameRun(reference, run) && referenceWindow.value === window.value) continue;
						const referenceData =
							dataByRunMetric[
								rawLayerKey(
									reference.jobId,
									reference.modelName,
									metric.value,
									referenceWindow.value
								)
							];
						if (!referenceData) continue;
						const key = deltaLayerKey(
							run.jobId,
							run.modelName,
							metric.value,
							window.value,
							reference.jobId,
							reference.modelName,
							referenceWindow.value
						);
						const { geojson, maxAbs } = buildDeltaGeojson(modelData, referenceData);
						entries.push({
							key,
							state: {
								layerId: mapLayerId(key),
								sourceId: mapSourceId(key),
								data: modelData,
								geojson,
								bounds: boundsFromGrid(modelData),
								stops: DIVERGING_STOPS,
								isDelta: true,
								deltaMaxAbs: maxAbs,
								referenceData,
								referenceModelName: reference.modelName
							}
						});
					}
				}
			}
		}
	}

	return entries;
}
