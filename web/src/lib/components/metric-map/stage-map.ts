/**
 * How the skill map opens on a workspace's benchmark stage, and what an exported
 * map says about the run: decided here so the map itself stays a general viewer.
 */
import { isFocusUnits, type Job, type JobMetrics } from '$lib/api';
import type { AreaExportContext } from './exportTitle';
import type { MapInitialView } from './initialView';
import { comparisonWindows, headlineWindow } from '$lib/metric-comparison';

/** Mean absolute error of the onset date, in days: the measure a forecaster asks for first. */
const ONSET_ERROR_METRIC = 'mean_mae';
const CLIMATOLOGY = 'climatology';
const NAMED_AREAS_LIMIT = 4;

/** What every map on a workspace stage shares: it is read on a page and printed. */
const STAGE_DEFAULTS = {
	basemap: 'carto-light',
	shortBasemapList: true,
	boundaryLabels: { adm1: 'Regions', adm2: 'Zones' }
} satisfies MapInitialView;

/** A blend's skill map and a forecast's onset map on the stage. */
export const BLEND_STAGE_VIEW: MapInitialView = { ...STAGE_DEFAULTS, boundaries: ['adm1'] };

/** The job of the model with the lowest onset error in a lead window; null when none was scored. */
export function bestModelJobId(metrics: JobMetrics[], window: string | undefined): string | null {
	const scored = metrics.flatMap((job) =>
		job.windows
			.filter((entry) => entry.window === window && entry.model !== CLIMATOLOGY)
			.flatMap((entry) => {
				const error = entry.metrics[ONSET_ERROR_METRIC]?.mean;
				return error == null ? [] : [{ jobId: job.job_id, error }];
			})
	);
	if (scored.length === 0) return null;
	return scored.reduce((best, next) => (next.error < best.error ? next : best)).jobId;
}

/**
 * Onset error for the best model as plain values, on a light basemap fitted to
 * what the run scored, with outlines at the level its areas were picked from.
 *
 * The best model is the one the run's headline names (`headlineJobId`), so the map
 * and the words beside the run agree. Without it, or on a lead window other than
 * the headline's, it is worked out here by the same rule.
 */
export function stageMapView(
	jobs: Job[],
	metrics: JobMetrics[],
	window?: string,
	headlineJobId?: string | null
): MapInitialView {
	const windows = comparisonWindows(metrics);
	const leadWindow = windows.find((each) => each === window) ?? headlineWindow(windows);
	const named =
		leadWindow === headlineWindow(windows) && jobs.some((job) => job.id === headlineJobId)
			? headlineJobId
			: null;
	const focus = jobs.map((job) => job.params?.focus_area).find(isFocusUnits);
	return {
		...STAGE_DEFAULTS,
		metric: ONSET_ERROR_METRIC,
		modelJobId: named ?? bestModelJobId(metrics, leadWindow) ?? undefined,
		viewMode: 'single',
		boundaries: focus?.level === 'adm2' ? ['adm1', 'adm2'] : ['adm1'],
		fitToScoredCells: true,
		robustColourRange: true,
		holdUserView: true
	};
}

/**
 * Whether the stage map opens afresh on its defaults: the first time, and when
 * another model finishes (the best model may have changed) — unless the reader has
 * changed the view by hand, which a late result must not undo.
 */
export function shouldRepickView(
	shownFor: string | null,
	finishedModels: string,
	changedByHand: boolean
): boolean {
	if (shownFor === null) return true;
	return shownFor !== finishedModels && !changedByHand;
}

/** The run's areas by name, or how many when the list would crowd a title; else its region. */
export function areaWords(
	areaOfInterest: string[] | null,
	region: string | null | undefined
): string {
	if (!areaOfInterest || areaOfInterest.length === 0) return region || 'Whole region';
	if (areaOfInterest.length <= NAMED_AREAS_LIMIT) return areaOfInterest.join(', ');
	return region ? `${areaOfInterest.length} areas in ${region}` : `${areaOfInterest.length} areas`;
}

/** `2015–2022` from the first and last dates scored; null when the jobs carry none. */
export function yearsWords(jobs: Pick<Job, 'params'>[]): string | null {
	const years = jobs.flatMap((job) =>
		[job.params?.start_date, job.params?.end_date].flatMap((date) => {
			const year = Number(date?.slice(0, 4));
			return Number.isInteger(year) && year > 0 ? [year] : [];
		})
	);
	if (years.length === 0) return null;
	const [first, last] = [Math.min(...years), Math.max(...years)];
	return first === last ? String(first) : `${first}–${last}`;
}
