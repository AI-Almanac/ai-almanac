import type { BoundaryLevel } from './types';

export function rawLayerKey(jobId: string, modelName: string, metric: string, window: string) {
	return `raw||${window}||${jobId}||${modelName}||${metric}`;
}

export function deltaLayerKey(
	jobId: string,
	modelName: string,
	metric: string,
	window: string,
	referenceJobId: string,
	referenceModelName: string,
	referenceWindow: string
) {
	return `delta||${window}||${jobId}||${modelName}||${metric}||${referenceWindow}||${referenceJobId}||${referenceModelName}`;
}

export function parseKey(key: string) {
	const [
		kind,
		window,
		jobId,
		modelName,
		metric,
		referenceWindow,
		referenceJobId,
		referenceModelName
	] = key.split('||');
	return {
		kind,
		window,
		jobId,
		modelName,
		metric,
		referenceWindow,
		referenceJobId,
		referenceModelName
	};
}

export function mapLayerId(key: string) {
	return `metric-layer-${key}`;
}

export function mapSourceId(key: string) {
	return `metric-source-${key}`;
}

export function boundaryLayerId(level: BoundaryLevel) {
	return `boundary-layer-${level}`;
}

export function boundarySourceId(level: BoundaryLevel) {
	return `boundary-source-${level}`;
}
