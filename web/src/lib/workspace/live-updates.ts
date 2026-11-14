/** A workspace kept current from its event stream. */
import type { WorkspaceArtifact, WorkspaceDetail, WorkspaceEvent } from '$lib/api/workspaces';

/** The workspace after one live update: jobs and analysis names are replaced, new files merged by id. */
export function applyWorkspaceEvent(
	workspace: WorkspaceDetail,
	event: Exclude<WorkspaceEvent, { type: 'follow_ups' }>
): WorkspaceDetail {
	if (event.type === 'jobs') return { ...workspace, jobs: event.jobs };
	if (event.type === 'analyses') return { ...workspace, analyses: event.analyses };
	const incoming = new Map(event.artifacts.map((artifact) => [artifact.id, artifact]));
	return {
		...workspace,
		artifacts: [
			...workspace.artifacts.filter((artifact) => !incoming.has(artifact.id)),
			...incoming.values()
		]
	};
}

/** When the newest file was made, so the stream sends only what came after it. */
export function newestArtifactAt(artifacts: WorkspaceArtifact[]): string | null {
	return artifacts.reduce<string | null>(
		(newest, artifact) => (newest && newest > artifact.created_at ? newest : artifact.created_at),
		null
	);
}
