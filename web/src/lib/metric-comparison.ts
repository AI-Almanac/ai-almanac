/**
 * One table that answers "which model did best here?": models down the side,
 * metrics across, for one lead-time window, with climatology as the yardstick.
 */
import type { JobMetrics, MetricDefinition } from '$lib/api';
import {
	formatMetricValue,
	isAnnualMaeMetric,
	lowerIsBetter,
	metricLabel,
	metricUnit,
	orderMetricKeys
} from './metric-metadata';
import { modelDisplayName } from './model-names';

export type ComparisonColumn = {
	metric: string;
	label: string;
	unit: string;
	lowerIsBetter: boolean | null;
};

export type ComparisonCell = { text: string; value: number | null; best: boolean };

export type ComparisonRow = {
	model: string;
	jobId: string | null;
	climatology: boolean;
	cells: ComparisonCell[];
};

export type Comparison = { window: string; columns: ComparisonColumn[]; rows: ComparisonRow[] };

const CLIMATOLOGY = 'climatology';

/** Enough of a job to name its model: the Benchmarks page's jobs or a workspace's. */
export type ModelNamed = {
	id: string;
	model_name: string | null;
	model_display_name?: string | null;
};

function windowOrder(window: string): number {
	if (window === 'all') return 10_000;
	const start = Number(window.split('-')[0]);
	return Number.isFinite(start) ? start : 20_000;
}

/** Shortest lead first, the whole season last; the one order every view lists windows in. */
export function compareWindows(a: string, b: string): number {
	return windowOrder(a) - windowOrder(b) || a.localeCompare(b);
}

const HEADLINE_WINDOW = '1-15';

/**
 * The window a run's headline skill is read from, as the server picks it: days
 * 1–15 when the run has it, else its shortest lead.
 */
export function headlineWindow(windows: string[]): string | undefined {
	if (windows.includes(HEADLINE_WINDOW)) return HEADLINE_WINDOW;
	return [...windows].sort(compareWindows)[0];
}

/** The lead-time windows the models were scored on, shortest first. */
export function comparisonWindows(metrics: JobMetrics[]): string[] {
	const windows = new Set(
		metrics.flatMap((m) => m.windows.filter((w) => w.model !== CLIMATOLOGY).map((w) => w.window))
	);
	return [...windows].sort(compareWindows);
}

function modelName(job: ModelNamed | undefined, model: string): string {
	return job?.model_display_name || modelDisplayName(job?.model_name ?? model);
}

export type ComparisonOptions = {
	/**
	 * Climatology can be the best on a metric and is marked when it is. Left out,
	 * only the models are compared with each other.
	 */
	baselineCompetes?: boolean;
};

export function compareModels(
	jobs: ModelNamed[],
	metrics: JobMetrics[],
	window: string,
	definitions: Map<string, MetricDefinition>,
	options: ComparisonOptions = {}
): Comparison {
	const byJob = new Map(jobs.map((job) => [job.id, job]));
	const rows = metrics.flatMap((m) =>
		m.windows
			.filter((w) => w.window === window && w.model !== CLIMATOLOGY)
			.map((w) => ({ model: modelName(byJob.get(m.job_id), w.model), jobId: m.job_id, w }))
	);
	const climatology = metrics
		.flatMap((m) => m.windows)
		.find((w) => w.window === window && w.model === CLIMATOLOGY);
	const baselineCompetes = options.baselineCompetes ?? false;
	const keys = orderMetricKeys(
		new Set(
			[...rows, ...(climatology ? [{ w: climatology }] : [])].flatMap((r) =>
				Object.keys(r.w.metrics)
			)
		),
		definitions
	).filter((metric) => !isAnnualMaeMetric(metric));
	const columns: ComparisonColumn[] = keys.map((metric) => {
		const unit = metricUnit(
			metric,
			[...rows, ...(climatology ? [{ w: climatology }] : [])].find((r) => r.w.metrics[metric])?.w
				.metrics[metric]?.unit,
			definitions
		);
		const lower = lowerIsBetter(metric, definitions) ?? (metric === 'bias' ? null : true);
		return { metric, label: metricLabel(metric, definitions), unit, lowerIsBetter: lower };
	});
	const best = columns.map((column) => {
		if (column.lowerIsBetter === null) return null;
		const contenders = [...rows.map((r) => r.w), ...(baselineCompetes ? [climatology] : [])];
		const values = contenders.flatMap((w) => w?.metrics[column.metric]?.mean ?? []);
		if (values.length === 0) return null;
		return column.lowerIsBetter ? Math.min(...values) : Math.max(...values);
	});
	const cells = (w: { metrics: Record<string, { mean: number }> }, markBest: boolean) =>
		columns.map((column, i) => {
			const value = w.metrics[column.metric]?.mean ?? null;
			return {
				value,
				text: formatMetricValue(value, column.unit),
				best: markBest && value !== null && value === best[i]
			};
		});
	return {
		window,
		columns,
		rows: [
			...rows
				.map((r) => ({
					model: r.model,
					jobId: r.jobId,
					climatology: false,
					cells: cells(r.w, true)
				}))
				.sort((a, b) => a.model.localeCompare(b.model)),
			...(climatology
				? [
						{
							model: 'Climatology',
							jobId: null,
							climatology: true,
							cells: cells(climatology, baselineCompetes)
						}
					]
				: [])
		]
	};
}
