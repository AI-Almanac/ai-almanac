import type { JobGridResponse } from '$lib/api';
import { parseKey } from './layerKeys';
import type { LayerState } from './types';

type TooltipArgs = {
	lat: number;
	lon: number;
	visibleKeys: Set<string>;
	layers: Record<string, LayerState>;
	metricLabel: (metricValue: string) => string;
	windowLabelFor: (windowValue: string | undefined) => string;
};

export function getValueAtLatLon(data: JobGridResponse, lat: number, lon: number): number | null {
	let bestI = 0;
	let bestJ = 0;
	let bestDist = Infinity;
	for (let i = 0; i < data.lats.length; i++) {
		for (let j = 0; j < data.lons.length; j++) {
			const d = Math.abs(data.lats[i] - lat) + Math.abs(data.lons[j] - lon);
			if (d < bestDist) {
				bestDist = d;
				bestI = i;
				bestJ = j;
			}
		}
	}
	return data.values[bestI]?.[bestJ] ?? null;
}

export function buildTooltipContent({
	lat,
	lon,
	visibleKeys,
	layers,
	metricLabel,
	windowLabelFor
}: TooltipArgs): string {
	const header = `<strong>${lat.toFixed(2)}°N ${lon.toFixed(2)}°E</strong>`;
	if (visibleKeys.size === 0) return header;

	const byModelOrder: string[] = [];
	const byModel: Record<string, string[]> = {};
	for (const key of visibleKeys) {
		if (!layers[key]) continue;
		const { modelName } = parseKey(key);
		if (!byModel[modelName]) {
			byModel[modelName] = [];
			byModelOrder.push(modelName);
		}
		byModel[modelName].push(key);
	}

	const sections: string[] = [header];
	for (const modelName of byModelOrder) {
		const keys = byModel[modelName];
		const displayName =
			modelName === 'climatology' ? 'Traditional Climatology' : modelName.toUpperCase();
		const rows = keys.map((key) => {
			const layer = layers[key];
			const { metric, window } = parseKey(key);
			const val = getValueAtLatLon(layer.data, lat, lon);
			const label = `${metricLabel(metric)} · ${windowLabelFor(window)}`;
			if (val == null) return `<span class="tt-metric">${label}: —</span>`;
			if (layer.isDelta && layer.referenceData) {
				const referenceVal = getValueAtLatLon(layer.referenceData, lat, lon);
				const delta = referenceVal != null ? val - referenceVal : null;
				const deltaStr =
					delta != null
						? ` <span class="tt-delta">(Δ${delta >= 0 ? '+' : ''}${delta.toFixed(3)})</span>`
						: '';
				return `<span class="tt-metric">${label}: ${val.toFixed(3)}${deltaStr}</span>`;
			}
			return `<span class="tt-metric">${label}: ${val.toFixed(3)}</span>`;
		});
		sections.push(
			`<div class="tt-group"><span class="tt-model">${displayName}</span>${rows.join('')}</div>`
		);
	}
	return sections.join('');
}
