import { untrack } from 'svelte';
import {
	getJobs,
	getJob,
	getJobMetrics,
	getJobGrid,
	getJobCell,
	getJobSkillScores,
	deleteJob,
	cancelJob,
	submitJob,
	type Job,
	type JobParams,
	type JobMetrics,
	type BboxFilter,
	type JobGridResponse,
	type JobCellResponse,
	type JobSkillScores
} from './api';

// Module-level cache so metrics survive component unmount/remount.
// Only caches the no-bbox baseline fetch — bbox-filtered requests always go to the server.
const _metricsCache = new Map<string, JobMetrics>();

/** Drop cached results for a job whose status changed or that was removed. */
export function invalidateJobCaches(jobId: string): void {
	_metricsCache.delete(jobId);
	_skillCache.delete(jobId);
	const prefix = `${jobId}||`;
	for (const key of _gridCache.keys()) {
		if (key.startsWith(prefix)) _gridCache.delete(key);
	}
	for (const key of _cellCache.keys()) {
		if (key.startsWith(prefix)) _cellCache.delete(key);
	}
}

export async function getCachedJobMetrics(jobId: string, bbox?: BboxFilter): Promise<JobMetrics> {
	if (!bbox) {
		const hit = _metricsCache.get(jobId);
		if (hit) return hit;
		const data = await getJobMetrics(jobId);
		_metricsCache.set(jobId, data);
		return data;
	}
	return getJobMetrics(jobId, bbox);
}

const _gridCache = new Map<string, JobGridResponse>();

export async function getCachedJobGrid(
	jobId: string,
	model: string,
	window: string,
	metric: string
): Promise<JobGridResponse> {
	const key = `${jobId}||${model}||${window}||${metric}`;
	const hit = _gridCache.get(key);
	if (hit) return hit;
	const data = await getJobGrid(jobId, model, window, metric);
	_gridCache.set(key, data);
	return data;
}

// Probabilistic skill scores. The endpoint returns an empty `windows` array for
// deterministic jobs rather than 404ing, so a miss caches like any other result
// and repeated tab switches don't re-request.
const _skillCache = new Map<string, JobSkillScores>();

export async function getCachedJobSkillScores(jobId: string): Promise<JobSkillScores> {
	const hit = _skillCache.get(jobId);
	if (hit) return hit;
	const data = await getJobSkillScores(jobId);
	_skillCache.set(jobId, data);
	return data;
}

const _cellCache = new Map<string, JobCellResponse>();

export async function getCachedJobCell(
	jobId: string,
	model: string,
	window: string,
	lat: number,
	lon: number
): Promise<JobCellResponse> {
	const key = `${jobId}||${model}||${window}||${lat}||${lon}`;
	const hit = _cellCache.get(key);
	if (hit) return hit;
	const data = await getJobCell(jobId, model, window, lat, lon);
	_cellCache.set(key, data);
	return data;
}

export type { Job };

export type RunGroup = {
	key: string; // `${eventType}||${region}||${start_date}||${end_date}`
	eventType: string;
	region: string;
	startDate: string;
	endDate: string;
	jobs: Job[];
	mostRecentAt: string; // for sort order
	isOwner: boolean; // true if the current user owns all jobs in this group
};

export type MultiRunFormData = {
	datasetId: string;
	modelNames: string[];
	sharedParams: JobParams;
	perModelOverrides?: Record<string, Partial<JobParams>>;
};

function jobRegionLabel(job: Job): string {
	return job.region_name ?? job.region_id ?? job.params?.region ?? 'Unknown';
}

function mergeJobs(incoming: Job[], existing: Job[]): Job[] {
	const incomingIds = new Set(incoming.map((job) => job.id));
	return [...incoming, ...existing.filter((job) => !incomingIds.has(job.id))];
}

function buildRunGroups(jobs: Job[]): RunGroup[] {
	const map = new globalThis.Map<string, Job[]>();
	for (const job of jobs) {
		const eventType = job.params?.event_type ?? 'monsoon_onset';
		const region = job.region_id ?? job.params?.region ?? 'unknown';
		const start = job.params?.start_date ?? 'unknown';
		const end = job.params?.end_date ?? 'unknown';
		// Group by run_id when available (set at submit time), fall back to param-based
		// key for jobs created before run_id was introduced.
		const key = job.run_id ?? `${eventType}||${region}||${start}||${end}`;
		if (!map.has(key)) map.set(key, []);
		map.get(key)!.push(job);
	}
	return [...map.entries()]
		.map(([key, groupJobs]) => {
			const mostRecentAt =
				groupJobs
					.map((j) => j.created_at ?? '')
					.sort()
					.at(-1) ?? '';
			const first = groupJobs[0];
			return {
				key,
				eventType: first.params?.event_type ?? 'monsoon_onset',
				region: jobRegionLabel(first),
				startDate: first.params?.start_date ?? '',
				endDate: first.params?.end_date ?? '',
				jobs: groupJobs,
				mostRecentAt,
				isOwner: groupJobs.every((j) => j.is_owner !== false)
			} satisfies RunGroup;
		})
		.sort((a, b) => b.mostRecentAt.localeCompare(a.mostRecentAt));
}

export class BenchmarkStore {
	jobs = $state<Job[]>([]);
	selectedGroupKey = $state<string | null>(null);
	showForm = $state(false);

	runGroups = $derived(buildRunGroups(this.jobs));
	selectedGroup = $derived(this.runGroups.find((g) => g.key === this.selectedGroupKey) ?? null);

	private pollTimer: ReturnType<typeof setInterval> | null = null;

	async load(groupKey?: string | null, selectDefault = true) {
		try {
			this.jobs = await getJobs();
		} catch (e) {
			console.error('Failed to fetch jobs', e);
		}
		const groups = buildRunGroups(this.jobs);
		if (groupKey) {
			const selected = groups.find((g) => g.key === groupKey);
			if (selected) {
				this.selectedGroupKey = selected.key;
				this.showForm = false;
			}
		} else if (selectDefault && !this.selectedGroupKey && groups.length > 0) {
			this.selectedGroupKey = groups[0].key;
			this.showForm = false;
		}
		this.startPolling();
	}

	selectGroup(key: string) {
		this.selectedGroupKey = key;
		this.showForm = false;
	}

	async deleteGroup(key: string) {
		const group = untrack(() => this.runGroups.find((g) => g.key === key));
		if (!group) return;
		await Promise.all(group.jobs.map((j) => deleteJob(j.id)));
		const removedIds = new Set(group.jobs.map((j) => j.id));
		for (const id of removedIds) invalidateJobCaches(id);
		this.jobs = untrack(() => this.jobs.filter((j) => !removedIds.has(j.id)));
		if (untrack(() => this.selectedGroupKey) === key) {
			const nextGroup = buildRunGroups(untrack(() => this.jobs))[0];
			this.selectedGroupKey = nextGroup?.key ?? null;
			this.showForm = false;
		}
	}

	async cancelGroup(key: string) {
		const group = untrack(() => this.runGroups.find((g) => g.key === key));
		if (!group) return;
		const active = group.jobs.filter((job) =>
			['queued', 'starting', 'running', 'canceling'].includes(job.status)
		);
		const updated = await Promise.all(active.map((job) => cancelJob(job.id)));
		const byId = new Map(updated.map((job) => [job.id, job]));
		this.jobs = untrack(() => this.jobs.map((job) => byId.get(job.id) ?? job));
	}

	startPolling() {
		if (this.pollTimer) return;
		this.pollTimer = setInterval(async () => {
			const active = untrack(() =>
				this.jobs.filter((j) => ['queued', 'starting', 'running', 'canceling'].includes(j.status))
			);
			if (active.length === 0) return;
			const updated = await Promise.all(active.map((j) => getJob(j.id)));
			for (const u of updated) {
				const idx = untrack(() => this.jobs.findIndex((j) => j.id === u.id));
				if (idx === -1) continue;
				if (untrack(() => this.jobs[idx].status) !== u.status) invalidateJobCaches(u.id);
				this.jobs[idx] = u;
			}
		}, 3000);
	}

	stopPolling() {
		if (this.pollTimer) {
			clearInterval(this.pollTimer);
			this.pollTimer = null;
		}
	}

	async submitRuns(data: MultiRunFormData): Promise<{ runId: string; jobs: Job[] }> {
		const runId = crypto.randomUUID();
		const results = await Promise.all(
			data.modelNames.map((modelName) =>
				submitJob({
					dataset_id: data.datasetId,
					model_name: modelName,
					params: {
						...data.sharedParams,
						...(data.perModelOverrides?.[modelName] ?? {})
					},
					run_id: runId
				})
			)
		);
		this.jobs = mergeJobs(
			results,
			untrack(() => this.jobs)
		);
		const first = results[0];
		const key =
			first.run_id ??
			`${first.params?.event_type ?? 'monsoon_onset'}||${first.region_id ?? first.params?.region ?? 'unknown'}||${first.params?.start_date ?? 'unknown'}||${first.params?.end_date ?? 'unknown'}`;
		this.selectedGroupKey = key;
		this.showForm = false;
		return { runId, jobs: results };
	}

	acceptSubmittedJobs(runId: string, jobs: Job[]): void {
		this.jobs = mergeJobs(
			jobs,
			untrack(() => this.jobs)
		);
		this.selectedGroupKey = runId;
		this.showForm = false;
	}

	/** Fetch metrics for a specific job (used for grid resolution chip) */
	getJobMetrics(id: string): Promise<JobMetrics> {
		return getJobMetrics(id);
	}
}

export { getJobMetrics };
export { fetchResultBlob } from './api';
export type { JobMetrics };
