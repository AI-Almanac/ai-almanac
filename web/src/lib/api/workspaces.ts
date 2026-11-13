// ---- Workspaces -------------------------------------------------------------
// Per-user projects grouping chat sessions, linked jobs and provenance-stamped
// artifacts. Types bound to the generated OpenAPI schema (api-types.gen.ts).
import type { components } from '../api-types.gen';
import { authHeaders } from '../auth';
import { sseEvents } from './chat';
import { BASE_URL, request } from './core';

export type Workspace = components['schemas']['WorkspaceOut'];
export type WorkspaceDetail = components['schemas']['WorkspaceDetail'];
export type WorkspaceJob = components['schemas']['WorkspaceJobOut'];
export type WorkspaceArtifact =
	components['schemas']['ai_almanac__server__routers__workspaces__ArtifactOut'];
export type ArtifactProvenance = components['schemas']['ProvenanceOut'];
export type ProvenanceStep = components['schemas']['ProvenanceStepOut'];
export type WorkspaceSummary = components['schemas']['WorkspaceSummary'];
export type SeasonSummary = components['schemas']['SeasonSummary'];
export type ForecastSummary = components['schemas']['ForecastSummary'];
export type SetupSummary = components['schemas']['SetupSummary'];
export type WorkspaceSkill = NonNullable<WorkspaceSummary['skill']>;

export async function listWorkspaces(): Promise<Workspace[]> {
	return request<Workspace[]>('/workspaces');
}

export async function createWorkspace(name: string, description = ''): Promise<Workspace> {
	return request<Workspace>('/workspaces', {
		method: 'POST',
		body: JSON.stringify({ name, description })
	});
}

export async function getWorkspace(id: string): Promise<WorkspaceDetail> {
	return request<WorkspaceDetail>(`/workspaces/${id}`);
}

/** Where the season stands, the setup, the latest forecast and the headline skill. */
export async function getWorkspaceSummary(id: string): Promise<WorkspaceSummary> {
	return request<WorkspaceSummary>(`/workspaces/${id}/summary`);
}

/** An existing run picked by hand, or one just started from the workspace. */
export type RunOrigin = 'added' | 'started';

export async function linkWorkspaceJob(
	id: string,
	jobId: string,
	origin: RunOrigin = 'added'
): Promise<WorkspaceJob[]> {
	return request<WorkspaceJob[]>(`/workspaces/${id}/jobs`, {
		method: 'POST',
		body: JSON.stringify({ job_id: jobId, origin })
	});
}

/** The user's own benchmarks, blends and forecasts that are not in the workspace yet. */
export async function listAddableJobs(id: string): Promise<WorkspaceJob[]> {
	return request<WorkspaceJob[]>(`/workspaces/${id}/addable-jobs`);
}

export async function getArtifactProvenance(
	workspaceId: string,
	artifactId: string,
	verify = false
): Promise<ArtifactProvenance> {
	return request<ArtifactProvenance>(
		`/workspaces/${workspaceId}/artifacts/${artifactId}/provenance?verify=${verify}`
	);
}

/** Rename a file in the workspace; `null` goes back to its default name. */
export async function renameWorkspaceArtifact(
	workspaceId: string,
	artifactId: string,
	title: string | null
): Promise<WorkspaceArtifact> {
	return request<WorkspaceArtifact>(`/workspaces/${workspaceId}/artifacts/${artifactId}`, {
		method: 'PATCH',
		body: JSON.stringify({ title })
	});
}

export async function fetchWorkspaceArtifact(
	workspaceId: string,
	artifact: { id: string }
): Promise<Blob> {
	const res = await fetch(
		`${BASE_URL}/workspaces/${workspaceId}/artifacts/${artifact.id}/download`,
		{
			headers: authHeaders()
		}
	);
	if (!res.ok) throw new Error(`Download failed (${res.status})`);
	return res.blob();
}

export async function downloadWorkspaceArtifact(
	workspaceId: string,
	artifact: WorkspaceArtifact
): Promise<void> {
	const url = URL.createObjectURL(await fetchWorkspaceArtifact(workspaceId, artifact));
	const a = document.createElement('a');
	a.href = url;
	a.download = `${artifact.label}.${artifact.format === 'netcdf' ? 'nc' : artifact.format}`;
	a.click();
	URL.revokeObjectURL(url);
}

export type WorkspaceEvent =
	{ type: 'jobs'; jobs: WorkspaceJob[] } | { type: 'artifacts'; artifacts: WorkspaceArtifact[] };

const RECONNECT_MS = { first: 1_000, max: 30_000 };

/**
 * Follow a workspace's job and artifact changes until `signal` aborts. The
 * server closes each stream after a few minutes, so this reconnects, passing
 * the newest artifact already held so nothing made in between is missed.
 */
export async function watchWorkspace(
	id: string,
	since: () => string | null,
	onEvent: (event: WorkspaceEvent) => void,
	signal: AbortSignal
): Promise<void> {
	let delay = RECONNECT_MS.first;
	while (!signal.aborted) {
		try {
			const newest = since();
			const query = newest ? `?since=${encodeURIComponent(newest)}` : '';
			const res = await fetch(`${BASE_URL}/workspaces/${id}/events${query}`, {
				headers: authHeaders(),
				signal
			});
			if (!res.ok) throw new Error(`Workspace updates failed (${res.status})`);
			delay = RECONNECT_MS.first;
			for await (const event of sseEvents<WorkspaceEvent>(res)) onEvent(event);
		} catch {
			if (signal.aborted) return;
			delay = Math.min(delay * 2, RECONNECT_MS.max);
		}
		await new Promise((resolve) => setTimeout(resolve, delay));
	}
}
