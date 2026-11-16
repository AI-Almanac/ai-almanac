import type { ChatArtifact, ChatSourceArtifact } from '$lib/api';
import type { ActivityGroup } from '$lib/chat/timeline';

/** A one-line stand-in for something too wide to read inside the conversation. */
export type ReplyCard = { name: string; kind: 'Figure' | 'File' };

type FileTitles = Record<string, string>;

/** Named as the workspace calls it now, then as the assistant labelled it; never by file name. */
export function figureCard(
	artifact: Pick<ChatArtifact, 'label' | 'workspace_artifact_id'>,
	fileTitles: FileTitles = {}
): ReplyCard {
	const title = artifact.workspace_artifact_id
		? fileTitles[artifact.workspace_artifact_id]
		: undefined;
	return { name: title || artifact.label || 'Figure', kind: 'Figure' };
}

export function sourceCard(source: ChatSourceArtifact, fileTitles: FileTitles = {}): ReplyCard {
	return { name: fileTitles[source.id] || source.label, kind: 'File' };
}

export function sourcesSummary(count: number): string {
	return `Based on ${count} ${count === 1 ? 'file' : 'files'}`;
}
