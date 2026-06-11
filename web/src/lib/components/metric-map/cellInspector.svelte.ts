import type { Job, JobCellResponse } from '$lib/api';
import { getCachedJobCell } from '$lib/benchmarks.svelte';

/** Owns the selected grid cell and the per-job metrics loaded for it. */
export class CellInspector {
	selected = $state<{ lat: number; lon: number } | null>(null);
	results = $state<JobCellResponse[]>([]);
	loading = $state(false);
	error = $state<string | null>(null);

	private requestId = 0;

	open(lat: number, lon: number) {
		this.selected = { lat, lon };
	}

	close() {
		this.requestId++;
		this.selected = null;
		this.results = [];
		this.error = null;
		this.loading = false;
	}

	async load(window: string, jobs: Job[]) {
		const cell = this.selected;
		if (!cell) return;
		const requestId = ++this.requestId;
		this.results = [];
		this.error = null;
		this.loading = true;
		try {
			const results = await Promise.all(
				jobs.map((job) => getCachedJobCell(job.id, job.model_name, window, cell.lat, cell.lon))
			);
			if (requestId !== this.requestId) return;
			this.results = results;
		} catch (e) {
			if (requestId !== this.requestId) return;
			this.error = e instanceof Error ? e.message : 'Failed to load cell metrics';
		} finally {
			if (requestId !== this.requestId) return;
			this.loading = false;
		}
	}
}
