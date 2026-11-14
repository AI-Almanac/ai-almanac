import type { ParsedFigure } from '$lib/result-parser';
import type { ChatArtifact, ChatMessage, ChatSession, ChatToolCall } from '$lib/api';
import { codeForToolCall } from './tools';

export async function copyCode(code: string) {
	await navigator.clipboard.writeText(code);
}

export function sessionLabel(s: ChatSession): string {
	return s.title || s.scope.title || `Chat ${new Date(s.created_at).toLocaleDateString()}`;
}

export type GalleryFigure = {
	artifactId: string;
	figure: ParsedFigure;
	toolName: string | null;
	code: string | null;
	createdAt: string;
};

export function artifactToFigure(artifact: ChatArtifact): ParsedFigure {
	const name = artifact.filename ?? `${artifact.id}.webp`;
	return {
		raw: {
			name,
			type: 'figure',
			url: artifact.url
		},
		kind: 'unknown',
		metric: null,
		model: null,
		window: null,
		label: artifact.label ?? name
	};
}

function artifactsForTurn(turn: ChatMessage): NonNullable<ChatMessage['artifacts']> {
	const byId = new Map<string, NonNullable<ChatMessage['artifacts']>[number]>();
	for (const artifact of turn.artifacts ?? []) byId.set(artifact.id, artifact);
	for (const toolCall of turn.tool_calls ?? []) {
		for (const artifact of toolCall.artifacts ?? []) {
			byId.set(artifact.id, artifact);
		}
	}
	return [...byId.values()];
}

/** Collect every figure produced in the given turns, newest definition winning. */
export function sessionFigures(turns: ChatMessage[]): GalleryFigure[] {
	const byId = new Map<string, GalleryFigure>();
	for (const turn of turns) {
		if (turn.role !== 'assistant') continue;
		const codeTools = (turn.tool_calls ?? []).filter((toolCall) => codeForToolCall(toolCall));
		for (const artifact of artifactsForTurn(turn)) {
			let sourceTool: ChatToolCall | null = null;
			for (const toolCall of turn.tool_calls ?? []) {
				if ((toolCall.artifacts ?? []).some((toolArtifact) => toolArtifact.id === artifact.id)) {
					sourceTool = toolCall;
					break;
				}
			}
			if (!sourceTool && codeTools.length === 1) {
				sourceTool = codeTools[0];
			}
			byId.set(artifact.id, {
				artifactId: artifact.id,
				figure: artifactToFigure(artifact),
				toolName: sourceTool?.name ?? null,
				code: sourceTool ? codeForToolCall(sourceTool) : null,
				createdAt: artifact.created_at
			});
		}
	}
	return [...byId.values()];
}
