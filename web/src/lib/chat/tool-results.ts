import { formatMetricValue, windowLabel } from '$lib/metric-metadata';
import { modelDisplayName } from '$lib/model-names';
import { modelLabel as blendMemberName } from '$lib/components/blend/blend-summary';

export type SubmittedJob = { id: string; label: string; status: string };

export type ResultTable = { caption: string; columns: string[]; rows: string[][] };

type Row = Record<string, unknown>;

export function isRecord(value: unknown): value is Row {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function records(value: unknown): Row[] {
	return Array.isArray(value) ? value.filter(isRecord) : [];
}

function text(value: unknown): string | null {
	return typeof value === 'string' && value.length > 0 ? value : null;
}

function number(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function jobLabel(job: Row): string {
	const modelName = text(job.model_name);
	return (
		text(job.model_display_name) ??
		(modelName ? modelDisplayName(modelName) : null) ??
		text(job.name) ??
		'Run'
	);
}

/** Jobs a submit tool reported, in the order they were submitted. */
export function submittedJobs(result: unknown): SubmittedJob[] {
	if (!isRecord(result)) return [];
	return records(result.jobs).flatMap((job) => {
		const id = text(job.id);
		return id ? [{ id, label: jobLabel(job), status: text(job.status) ?? 'queued' }] : [];
	});
}

export function liveJobStatus(job: SubmittedJob, statuses?: Record<string, string>): string {
	return statuses?.[job.id] ?? job.status;
}

export function allJobsComplete(jobs: SubmittedJob[], statuses?: Record<string, string>): boolean {
	return jobs.length > 0 && jobs.every((job) => liveJobStatus(job, statuses) === 'complete');
}

/** The error a step reported in its result, or null when it reported none. */
export function resultError(result: unknown): string | null {
	if (!isRecord(result)) return null;
	return typeof result.error === 'string' ? result.error : null;
}

const SPATIAL_METRICS: [string, string][] = [
	['false_alarm_rate', 'False alarms'],
	['miss_rate', 'Misses'],
	['mae', 'Mean absolute error']
];

export function spatialMetricsTable(result: Row): ResultTable | null {
	const windows = records(result.windows);
	const present = SPATIAL_METRICS.filter(([key]) =>
		windows.some((window) => isRecord(window.metrics) && isRecord(window.metrics[key]))
	);
	if (windows.length === 0 || present.length === 0) return null;
	return {
		caption: 'Averages across the region',
		columns: ['Model', 'Forecast days', ...present.map(([, label]) => label)],
		rows: windows.map((window) => {
			const metrics = isRecord(window.metrics) ? window.metrics : {};
			return [
				modelDisplayName(text(window.model) ?? ''),
				windowLabel(text(window.window) ?? ''),
				...present.map(([key]) => {
					const stats = isRecord(metrics[key]) ? metrics[key] : {};
					return formatMetricValue(number(stats.mean), text(stats.unit));
				})
			];
		})
	};
}

const SKILL_CAPTION = 'Skill against climatology: above 0 beats it, below 0 is worse';

function skill(value: unknown): string {
	return formatMetricValue(number(value), 'dimensionless');
}

export function skillScoresTable(result: Row): ResultTable | null {
	const windows = records(result.windows);
	if (windows.length === 0) return null;
	return {
		caption: SKILL_CAPTION,
		columns: ['Model', 'Forecast days', 'Ranked probability skill', 'Brier skill', 'ROC area'],
		rows: windows.map((window) => {
			const overall = isRecord(window.overall) ? window.overall : {};
			return [
				modelDisplayName(text(window.model) ?? ''),
				windowLabel(text(window.window) ?? ''),
				skill(overall.ranked_probability_skill_score),
				skill(overall.brier_skill_score),
				skill(overall.auc)
			];
		})
	};
}

export function blendSkillTable(result: Row): ResultTable | null {
	const members = records(result.skill);
	if (members.length === 0) return null;
	return {
		caption: SKILL_CAPTION,
		columns: ['Model', 'Ranked probability skill', 'Brier skill', 'ROC area'],
		rows: members.map((row) => [
			blendMemberName(text(row.model) ?? ''),
			skill(row.rps_skill),
			skill(row.brier_skill),
			skill(row.auc)
		])
	};
}
