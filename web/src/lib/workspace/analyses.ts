import type { AnalysisName, ChatMessage, WorkspaceArtifact } from '$lib/api';
import { isFollowUpTurn } from '$lib/chat/follow-ups';

/** The files one chat turn made; `turnId` is null for files added outside the chat. */
export type Analysis = {
	key: string;
	turnId: string | null;
	files: WorkspaceArtifact[];
	startedAt: string;
};

const OUTSIDE_CHAT = 'outside-chat';

function earliest(files: WorkspaceArtifact[]): string {
	return files.reduce((min, file) => (file.created_at < min ? file.created_at : min), '￿');
}

/** Files bucketed by the turn that made them, newest turn first. */
export function groupByTurn(files: WorkspaceArtifact[]): Analysis[] {
	const byTurn = new Map<string, WorkspaceArtifact[]>();
	for (const file of files) {
		const key = file.turn_id ?? OUTSIDE_CHAT;
		byTurn.set(key, [...(byTurn.get(key) ?? []), file]);
	}
	return [...byTurn.entries()]
		.map(([key, group]) => ({
			key,
			turnId: key === OUTSIDE_CHAT ? null : key,
			files: group,
			startedAt: earliest(group)
		}))
		.sort((a, b) => b.startedAt.localeCompare(a.startedAt));
}

/**
 * What the user asked that led to `turnId`: the nearest earlier message they
 * wrote. Follow-up turns are skipped, since they continue an earlier question.
 */
export function questionFor(turnId: string, transcript: ChatMessage[]): string | null {
	const index = transcript.findIndex((message) => message.id === turnId);
	if (index < 0) return null;
	const asked = transcript
		.slice(0, index + 1)
		.findLast((message) => message.role === 'user' && !isFollowUpTurn(message));
	return asked?.content.trim() || null;
}

export type AnalysisHeading = {
	text: string;
	/** The full question behind the analysis, for when a short name stands in for it. */
	question: string | null;
};

/** An analysis goes by its name once it has one, and by the question that led to it until then. */
export function analysisHeading(
	analysis: Analysis,
	transcript: ChatMessage[],
	names: AnalysisName[]
): AnalysisHeading {
	if (!analysis.turnId) return { text: 'Added outside the chat', question: null };
	const question = questionFor(analysis.turnId, transcript);
	const name = names.find((entry) => entry.turn_id === analysis.turnId)?.title;
	const fallback = `Analysis from ${new Date(analysis.startedAt).toLocaleString()}`;
	return { text: name ?? question ?? fallback, question };
}

/** The analysis names after one is set (`named`) or cleared (`null`). */
export function withAnalysisName(
	names: AnalysisName[],
	turnId: string,
	named: AnalysisName | null
): AnalysisName[] {
	const others = names.filter((entry) => entry.turn_id !== turnId);
	return named ? [...others, named] : others;
}
