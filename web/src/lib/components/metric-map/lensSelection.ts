import { deltaLayerKey, rawLayerKey } from './layerKeys';
import type {
	MapViewMode,
	MetricDef,
	MetricWindowAvailability,
	MetricWindowAvailabilityByJob,
	RunDef,
	WindowDef
} from './types';

export type LensSelection = {
	viewMode: MapViewMode;
	selectedMetric: string;
	selectedModelJobId: string;
	selectedReferenceJobId: string;
	selectedWindow: string;
	selectedReferenceWindow: string;
};

type LensSelectionContext = {
	activeRuns: RunDef[];
	activeWindows: WindowDef[];
	forecastWindow: string;
	metrics: MetricDef[];
	metricWindowAvailability?: MetricWindowAvailability;
	metricWindowAvailabilityByJob?: MetricWindowAvailabilityByJob;
};

export function availableModelRuns(activeRuns: RunDef[]) {
	return activeRuns.filter((run) => run.modelName !== 'climatology');
}

export function sameRun(a: RunDef, b: RunDef) {
	return a.jobId === b.jobId && a.modelName === b.modelName;
}

export function selectedModelRun(selection: LensSelection, activeRuns: RunDef[]) {
	const modelRuns = availableModelRuns(activeRuns);
	return modelRuns.find((run) => run.jobId === selection.selectedModelJobId) ?? modelRuns[0];
}

export function selectedReferenceRun(selection: LensSelection, activeRuns: RunDef[]) {
	if (selection.selectedReferenceJobId === 'climatology') {
		return activeRuns.find((run) => run.modelName === 'climatology');
	}
	return availableModelRuns(activeRuns).find(
		(run) => run.jobId === selection.selectedReferenceJobId
	);
}

export function normalizeLensSelection(
	selection: LensSelection,
	{
		activeRuns,
		activeWindows,
		forecastWindow,
		metrics,
		metricWindowAvailability,
		metricWindowAvailabilityByJob
	}: LensSelectionContext
): LensSelection {
	const modelRuns = availableModelRuns(activeRuns);
	const next = { ...selection };
	if (!next.selectedModelJobId || !modelRuns.some((run) => run.jobId === next.selectedModelJobId)) {
		next.selectedModelJobId = modelRuns[0]?.jobId ?? '';
	}
	const selectedModelAvailability =
		metricWindowAvailabilityByJob?.[next.selectedModelJobId] ?? metricWindowAvailability;
	const availableWindowsForMetric = (metric: string) =>
		activeWindows.filter(
			(window) =>
				!selectedModelAvailability || selectedModelAvailability[metric]?.includes(window.value)
		);
	const availableMetricsForWindow = (window: string) =>
		metrics.filter(
			(metric) =>
				!selectedModelAvailability || selectedModelAvailability[metric.value]?.includes(window)
		);

	if (
		!next.selectedWindow ||
		!activeWindows.some((window) => window.value === next.selectedWindow)
	) {
		next.selectedWindow = forecastWindow;
	}
	if (
		!next.selectedReferenceWindow ||
		!activeWindows.some((window) => window.value === next.selectedReferenceWindow)
	) {
		next.selectedReferenceWindow = next.selectedWindow;
	}
	if (
		!next.selectedMetric ||
		!metrics.some((metric) => metric.value === next.selectedMetric) ||
		!availableMetricsForWindow(next.selectedWindow).some(
			(metric) => metric.value === next.selectedMetric
		)
	) {
		next.selectedMetric =
			availableMetricsForWindow(next.selectedWindow)[0]?.value ?? metrics[0]?.value ?? '';
	}
	if (
		next.selectedMetric &&
		!availableWindowsForMetric(next.selectedMetric).some(
			(window) => window.value === next.selectedWindow
		)
	) {
		next.selectedWindow =
			availableWindowsForMetric(next.selectedMetric)[0]?.value ?? forecastWindow;
	}
	if (
		next.selectedMetric &&
		!availableWindowsForMetric(next.selectedMetric).some(
			(window) => window.value === next.selectedReferenceWindow
		)
	) {
		next.selectedReferenceWindow = next.selectedWindow;
	}
	if (
		next.selectedReferenceJobId !== 'climatology' &&
		!modelRuns.some((run) => run.jobId === next.selectedReferenceJobId)
	) {
		next.selectedReferenceJobId = 'climatology';
	}
	if (next.selectedReferenceJobId === next.selectedModelJobId) {
		const climatologyRun = activeRuns.find((run) => run.modelName === 'climatology');
		if (next.selectedReferenceWindow === next.selectedWindow) {
			next.selectedReferenceJobId =
				climatologyRun?.jobId === next.selectedModelJobId
					? (modelRuns.find((run) => run.jobId !== next.selectedModelJobId)?.jobId ?? '')
					: 'climatology';
		}
	}

	return next;
}

export function currentLensKey(selection: LensSelection, activeRuns: RunDef[]) {
	const modelRun = selectedModelRun(selection, activeRuns);
	if (!modelRun || !selection.selectedMetric) return null;
	if (selection.viewMode === 'single') {
		return rawLayerKey(
			modelRun.jobId,
			modelRun.modelName,
			selection.selectedMetric,
			selection.selectedWindow
		);
	}
	const referenceRun =
		selection.viewMode === 'baseline'
			? activeRuns.find((run) => run.modelName === 'climatology')
			: selectedReferenceRun(selection, activeRuns);
	const referenceWindow =
		selection.viewMode === 'baseline'
			? selection.selectedWindow
			: selection.selectedReferenceWindow;
	if (
		!referenceRun ||
		(sameRun(referenceRun, modelRun) && referenceWindow === selection.selectedWindow)
	) {
		return rawLayerKey(
			modelRun.jobId,
			modelRun.modelName,
			selection.selectedMetric,
			selection.selectedWindow
		);
	}
	return deltaLayerKey(
		modelRun.jobId,
		modelRun.modelName,
		selection.selectedMetric,
		selection.selectedWindow,
		referenceRun.jobId,
		referenceRun.modelName,
		referenceWindow
	);
}

export function currentLensKeys(selection: LensSelection, activeRuns: RunDef[]) {
	if (selection.viewMode !== 'swipe') {
		const key = currentLensKey(selection, activeRuns);
		return key ? [key] : [];
	}
	const modelRun = selectedModelRun(selection, activeRuns);
	const referenceRun = selectedReferenceRun(selection, activeRuns);
	if (
		!modelRun ||
		!referenceRun ||
		!selection.selectedMetric ||
		(sameRun(referenceRun, modelRun) &&
			selection.selectedReferenceWindow === selection.selectedWindow)
	) {
		const key = currentLensKey(selection, activeRuns);
		return key ? [key] : [];
	}
	return [
		rawLayerKey(
			modelRun.jobId,
			modelRun.modelName,
			selection.selectedMetric,
			selection.selectedWindow
		),
		rawLayerKey(
			referenceRun.jobId,
			referenceRun.modelName,
			selection.selectedMetric,
			selection.selectedReferenceWindow
		)
	];
}

export function swipeRuns(selection: LensSelection, activeRuns: RunDef[]) {
	if (selection.viewMode !== 'swipe') return null;
	const modelRun = selectedModelRun(selection, activeRuns);
	const referenceRun = selectedReferenceRun(selection, activeRuns);
	if (
		!modelRun ||
		!referenceRun ||
		(sameRun(modelRun, referenceRun) &&
			selection.selectedWindow === selection.selectedReferenceWindow)
	) {
		return null;
	}
	return { left: modelRun, right: referenceRun };
}

export function lensSelectionsEqual(a: LensSelection, b: LensSelection) {
	return (
		a.viewMode === b.viewMode &&
		a.selectedMetric === b.selectedMetric &&
		a.selectedModelJobId === b.selectedModelJobId &&
		a.selectedReferenceJobId === b.selectedReferenceJobId &&
		a.selectedWindow === b.selectedWindow &&
		a.selectedReferenceWindow === b.selectedReferenceWindow
	);
}
