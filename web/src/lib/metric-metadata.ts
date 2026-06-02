import { getMetricDefinitions, type MetricDefinition } from '$lib/api';

export type MetricOption = {
	value: string;
	label: string;
};

const FALLBACK_ORDER = ['false_alarm_rate', 'miss_rate', 'mean_mae', 'rmse', 'mae', 'bias', 'acc'];

let cachedDefinitions: Promise<MetricDefinition[]> | null = null;

export function loadMetricDefinitions(): Promise<MetricDefinition[]> {
	cachedDefinitions ??= getMetricDefinitions();
	return cachedDefinitions;
}

export function metricMap(definitions: MetricDefinition[]): Map<string, MetricDefinition> {
	return new Map(definitions.map((definition) => [definition.id, definition]));
}

export function isAnnualMaeMetric(metric: string): boolean {
	return /^mae_\d{4}$/.test(metric);
}

export function metricLabel(
	metric: string,
	definitions: MetricDefinition[] | Map<string, MetricDefinition>
): string {
	const definition =
		definitions instanceof Map ? definitions.get(metric) : metricMap(definitions).get(metric);
	if (definition) return definition.label;
	const annual = metric.match(/^mae_(\d{4})$/);
	if (annual) return `Mean absolute error ${annual[1]}`;
	return metric
		.split('_')
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(' ');
}

export function metricUnit(
	metric: string,
	apiUnit: string | null | undefined,
	definitions: MetricDefinition[] | Map<string, MetricDefinition>
): string {
	const definition =
		definitions instanceof Map ? definitions.get(metric) : metricMap(definitions).get(metric);
	return (
		definition?.unit ??
		apiUnit ??
		(metric === 'false_alarm_rate' || metric === 'miss_rate' ? 'fraction' : 'days')
	);
}

export function formatMetricValue(
	value: number | null | undefined,
	unit: string | null | undefined
): string {
	if (value == null) return '—';
	if (unit === 'fraction') return `${(value * 100).toFixed(1)}%`;
	if (unit === 'days') return `${value.toFixed(1)} d`;
	if (unit === 'mm') return `${value.toFixed(2)} mm`;
	if (unit === 'dimensionless' || unit == null) return value.toFixed(3);
	return `${value.toFixed(2)} ${unit}`;
}

export function formatMetricDelta(
	value: number | null | undefined,
	unit: string | null | undefined
): string {
	if (value == null) return '—';
	const formatted = formatMetricValue(Math.abs(value), unit);
	return `${value >= 0 ? '+' : '-'}${formatted}`;
}

export function metricSortValue(
	metric: string,
	definitions: MetricDefinition[] | Map<string, MetricDefinition>
): number {
	const ids =
		definitions instanceof Map
			? [...definitions.keys()]
			: definitions.map((definition) => definition.id);
	const index = ids.indexOf(metric);
	if (index >= 0) return index;
	const fallbackIndex = FALLBACK_ORDER.indexOf(metric);
	if (fallbackIndex >= 0) return ids.length + fallbackIndex;
	if (isAnnualMaeMetric(metric))
		return ids.length + FALLBACK_ORDER.length + 1000 + Number(metric.slice(4));
	return ids.length + FALLBACK_ORDER.length + 2000;
}

export function orderMetricKeys(
	metrics: Iterable<string>,
	definitions: MetricDefinition[] | Map<string, MetricDefinition>
): string[] {
	return [...metrics].sort((a, b) => {
		const byOrder = metricSortValue(a, definitions) - metricSortValue(b, definitions);
		return byOrder || a.localeCompare(b);
	});
}

export function lowerIsBetter(
	metric: string,
	definitions: MetricDefinition[] | Map<string, MetricDefinition>
): boolean | null {
	if (metric === 'bias') return null;
	const definition =
		definitions instanceof Map ? definitions.get(metric) : metricMap(definitions).get(metric);
	return definition?.lower_is_better ?? null;
}

export function metricOptions(
	metrics: Iterable<string>,
	definitions: MetricDefinition[] | Map<string, MetricDefinition>
): MetricOption[] {
	return orderMetricKeys(metrics, definitions)
		.filter((metric) => !isAnnualMaeMetric(metric))
		.map((metric) => ({ value: metric, label: metricLabel(metric, definitions) }));
}

export function windowLabel(window: string): string {
	if (window === 'all') return 'All days';
	return `Days ${window.replace('-', '–')}`;
}
