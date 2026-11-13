import type { JobMetrics, MetricStats } from '../../src/lib/api';

function stat(mean: number, unit: string): MetricStats {
	return { mean, min: 0, max: 0, p25: 0, p50: 0, p75: 0, p90: 0, unit };
}

/**
 * One job's scores: the mean onset error of `model`, and of the climatology
 * baseline when one is given, for each lead window.
 */
export function metricsFor(
	jobId: string,
	model: string,
	errors: Record<string, number>,
	climatology: Record<string, number> = {}
): JobMetrics {
	const rows = (who: string, byWindow: Record<string, number>) =>
		Object.entries(byWindow).map(([window, mean]) => ({
			model: who,
			window,
			tolerance_days: null,
			metrics: { mean_mae: stat(mean, 'days') }
		}));
	return {
		job_id: jobId,
		grid: null,
		bbox: null,
		windows: [...rows(model, errors), ...rows('climatology', climatology)]
	};
}
