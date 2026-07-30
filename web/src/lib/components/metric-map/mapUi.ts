import type { MapViewMode, RunDef } from './types';

export function isHigherBetterMetric(metricValue: string): boolean {
	return metricValue === 'acc';
}

export function isNeutralDeltaMetric(metricValue: string): boolean {
	return metricValue === 'bias';
}

export function modelDisplayName(modelName: string) {
	const labels: Record<string, string> = {
		fuxi: 'FuXi',
		aifs: 'AIFS',
		aifs_daily: 'AIFS Daily',
		fuxi_s2s: 'FuXi S2S',
		climatology: 'Traditional Climatology'
	};
	return labels[modelName.toLowerCase()] ?? modelName;
}

export function modelRunLabel(run: RunDef) {
	return run.displayName || modelDisplayName(run.modelName);
}

export function viewModeDescription(mode: MapViewMode) {
	if (mode === 'single') return 'Show raw metric values for one model.';
	if (mode === 'baseline')
		return 'Show where the selected model improves or worsens relative to Traditional Climatology.';
	if (mode === 'difference')
		return 'Show the selected model and lead time minus the comparison choice.';
	return 'Compare two raw metric maps across models or lead times with a draggable split view.';
}
