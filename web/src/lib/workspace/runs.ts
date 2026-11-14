import type { WorkspaceArtifact, WorkspaceJob } from '$lib/api/workspaces';
import { modelDisplayName } from '$lib/model-names';
import { groupByTurn, type Analysis } from './analyses';
import {
	FULL_NAME,
	LIST_NAME,
	TITLE_NAME,
	NO_SCOPE,
	nameParts,
	type NameDetail,
	type RunNaming
} from './run-names';
import { forecastFact, skillFact } from './summary';

/** Jobs submitted together (sharing a run id) and the files they produced. */
export type Run = {
	key: string;
	jobs: WorkspaceJob[];
	files: WorkspaceArtifact[];
};

export type FileFamily = {
	name: string;
	files: WorkspaceArtifact[];
};

export type WorkspaceResults = {
	runs: Run[];
	/** Files not made by a run, grouped by the chat turn that made them. */
	analyses: Analysis[];
};

function runKeyOf(job: WorkspaceJob): string {
	return job.run_id ?? job.id;
}

function latestCreated(run: Run): string {
	return run.jobs.reduce((latest, job) => (job.created_at > latest ? job.created_at : latest), '');
}

export function groupByRun(jobs: WorkspaceJob[], artifacts: WorkspaceArtifact[]): WorkspaceResults {
	const runs = new Map<string, Run>();
	const runKeyByJob = new Map<string, string>();
	for (const job of jobs) {
		const key = runKeyOf(job);
		runKeyByJob.set(job.id, key);
		const run = runs.get(key) ?? { key, jobs: [], files: [] };
		runs.set(key, { ...run, jobs: [...run.jobs, job] });
	}

	const unplaced: WorkspaceArtifact[] = [];
	for (const artifact of artifacts) {
		const keys = runKeysOf(artifact, runKeyByJob);
		for (const key of keys) {
			const run = runs.get(key)!;
			runs.set(key, { ...run, files: [...run.files, artifact] });
		}
		if (keys.length === 0) unplaced.push(artifact);
	}

	const newestFirst = [...runs.values()].sort((a, b) =>
		latestCreated(b).localeCompare(latestCreated(a))
	);
	const analysed = artifacts.filter(
		(artifact) => isDerived(artifact) || unplaced.includes(artifact)
	);
	return { runs: newestFirst, analyses: groupByTurn(analysed) };
}

/** Made by the assistant or by hand from other files, rather than output by a run itself. */
function isDerived(artifact: WorkspaceArtifact): boolean {
	return !artifact.job_id;
}

/** Every linked run the file came from: its own run, or the runs of its inputs. */
function runKeysOf(artifact: WorkspaceArtifact, runKeyByJob: Map<string, string>): string[] {
	const jobIds = artifact.run_job_ids?.length
		? artifact.run_job_ids
		: artifact.job_id
			? [artifact.job_id]
			: [];
	return unique(jobIds.flatMap((jobId) => runKeyByJob.get(jobId) ?? []));
}

function familyName(artifact: WorkspaceArtifact): string {
	if (artifact.group) return artifact.group;
	return artifact.kind ? artifact.kind[0].toUpperCase() + artifact.kind.slice(1) : 'Other';
}

/** Files bucketed by their family (e.g. "Spatial metrics"), in first-seen order. */
export function groupByFamily(artifacts: WorkspaceArtifact[]): FileFamily[] {
	const families = new Map<string, WorkspaceArtifact[]>();
	for (const artifact of artifacts) {
		const name = familyName(artifact);
		families.set(name, [...(families.get(name) ?? []), artifact]);
	}
	return [...families.entries()].map(([name, files]) => ({ name, files }));
}

function unique<T>(values: T[]): T[] {
	return [...new Set(values)];
}

function runModels(run: Run): string[] {
	return unique(
		run.jobs.flatMap((job) => (job.model_name ? [modelDisplayName(job.model_name)] : []))
	);
}

export type RunKind = 'benchmark' | 'blend' | 'forecast';

/** A job is a blend or a forecast by its type; any other job scored models, as a benchmark does. */
export function jobKind(job: Pick<WorkspaceJob, 'job_type'>): RunKind {
	return job.job_type === 'blend' || job.job_type === 'forecast' ? job.job_type : 'benchmark';
}

export const RUN_KIND_LABEL: Record<RunKind, string> = {
	benchmark: 'Benchmark',
	blend: 'Blend',
	forecast: 'Forecast'
};

export function runKind(run: Run): RunKind {
	const kinds = run.jobs.map(jobKind);
	if (kinds.includes('forecast')) return 'forecast';
	return kinds.includes('blend') ? 'blend' : 'benchmark';
}

/** The run's finished jobs of a kind, with something to show: a raw forecast (no blend) has no onset map. */
export function finishedJobs(run: Run, kind: RunKind): WorkspaceJob[] {
	return run.jobs.filter(
		(job) =>
			jobKind(job) === kind && job.status === 'complete' && (kind !== 'forecast' || job.blended)
	);
}

/** The interactive view a run opens to, named as the user would ask for it. */
const RUN_VIEW_NAME: Record<RunKind, string> = {
	benchmark: 'Skill maps',
	blend: 'Skill map',
	forecast: 'Onset map'
};

/** What opening the run shows beyond its files, or null until a job has something to show. */
export function runView(run: Run): string | null {
	const kind = runKind(run);
	return finishedJobs(run, kind).length > 0 ? RUN_VIEW_NAME[kind] : null;
}

/**
 * The run the stage opens on before anything is chosen: the newest run with a
 * view, else the newest run at all (still running, so its files and notice).
 */
export function defaultRun(runs: Run[]): Run | null {
	return runs.find((run) => runView(run) !== null) ?? runs[0] ?? null;
}

/** The lead-time window a spatial metrics file holds, e.g. "1-15", which its map opens on. */
export function metricsWindow(file: WorkspaceArtifact): string | null {
	return /^(?:e2s_)?spatial_metrics_.+_(\d+-\d+)$/.exec(file.label)?.[1] ?? null;
}

/** The run a file belongs to, if any. */
export function runOf(file: WorkspaceArtifact, runs: Run[]): Run | null {
	return runs.find((run) => run.files.some((each) => each.id === file.id)) ?? null;
}

export type RunSection = { kind: RunKind; title: string; runs: Run[] };

/** Benchmarks, blends, then forecasts; kinds with no runs are left out. */
export function runsByKind(runs: Run[]): RunSection[] {
	const sections: RunSection[] = [
		{ kind: 'benchmark', title: 'Benchmarks', runs: [] },
		{ kind: 'blend', title: 'Blends', runs: [] },
		{ kind: 'forecast', title: 'Forecasts', runs: [] }
	];
	for (const run of runs) sections.find((section) => section.kind === runKind(run))!.runs.push(run);
	return sections.filter((section) => section.runs.length > 0);
}

/** A blend's job carries the blend's own name rather than a model's. */
function blendNames(run: Run): string[] {
	return unique(run.jobs.flatMap((job) => (job.model_name ? [job.model_name] : [])));
}

function saysAnything(scope: WorkspaceJob['scope']): boolean {
	return Boolean(scope.place || scope.years || scope.lead_days);
}

function runNaming(run: Run): RunNaming {
	const isBlend = runKind(run) === 'blend';
	return {
		ownName: isBlend ? blendNames(run).join(', ') || null : null,
		scope: run.jobs.map((job) => job.scope).find(saysAnything) ?? NO_SCOPE,
		areas: run.jobs.find((job) => job.area_of_interest?.length)?.area_of_interest ?? null,
		onset: run.jobs.find((job) => job.onset_definition)?.onset_definition ?? null,
		models: isBlend ? [] : runModels(run)
	};
}

/** A run's name at the given level of detail, or its kind when nothing names it. */
function runName(run: Run, detail: NameDetail): string {
	return nameParts(runNaming(run), detail).join(' · ') || RUN_KIND_LABEL[runKind(run)];
}

/**
 * The stage's title, e.g. "Ethiopia · 1998–2024 · Two-stage · AIFS, FuXi · days 1–30"
 * for a benchmark, or "AIFS-FuXi Ethiopia Blend · Ethiopia · 2008–2024" for a blend.
 */
export function runTitle(run: Run): string {
	return runName(run, TITLE_NAME);
}

/**
 * What each run is called in the products list, in the order given: the short
 * name, spelled out in full for runs whose short names would otherwise match.
 */
export function runListLabels(runs: Run[]): string[] {
	const short = runs.map((run) => runName(run, LIST_NAME));
	const shared = (label: string) => short.filter((each) => each === label).length > 1;
	return runs.map((run, index) => (shared(short[index]) ? runName(run, FULL_NAME) : short[index]));
}

export function findRunByJobs(runs: Run[], jobIds: string[]): Run | null {
	const wanted = new Set(jobIds);
	return runs.find((run) => run.jobs.some((job) => wanted.has(job.id))) ?? null;
}

/** The chat refers to files by id or by label; ids win when both match. */
export function resolveArtifact(
	artifacts: WorkspaceArtifact[],
	ref: string
): WorkspaceArtifact | null {
	return (
		artifacts.find((artifact) => artifact.id === ref) ??
		artifacts.find((artifact) => artifact.label === ref) ??
		null
	);
}

export function jobStatusMap(jobs: WorkspaceJob[]): Record<string, string> {
	return Object.fromEntries(jobs.map((job) => [job.id, job.status]));
}

/**
 * What a result row leads with, beside its title: the blend's skill, the best
 * model of a benchmark run, or a forecast's issue dates. Null until the server
 * has read it.
 */
export function runHeadline(run: Run): string | null {
	const headline = run.jobs.find((job) => job.headline)?.headline;
	if (!headline) return null;
	return headline.kind === 'forecast'
		? forecastFact(headline).headline
		: skillFact(headline).headline;
}
