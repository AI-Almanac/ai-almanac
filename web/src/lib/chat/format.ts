import { marked } from 'marked';
import DOMPurify from 'dompurify';
import type { ParsedFigure } from '$lib/result-parser';
import type { ChatArtifact, ChatMessage, ChatSession, ChatToolCall } from '$lib/api';

export function renderMarkdown(text: string): string {
	return DOMPurify.sanitize(marked.parse(text) as string);
}

const CODE_TOOLS = new Set(['run_code_sandbox', 'run_code']);

type ToolLabel = { active: string; done: string };

const TOOL_LABELS: Record<string, ToolLabel> = {
	list_regions: { active: 'Checking regions', done: 'Checked regions' },
	list_datasets: { active: 'Checking datasets', done: 'Checked datasets' },
	list_models: { active: 'Checking models', done: 'Checked models' },
	get_benchmark_config: { active: 'Reading the benchmark plan', done: 'Read the benchmark plan' },
	update_benchmark_config: {
		active: 'Updating the benchmark plan',
		done: 'Updated the benchmark plan'
	},
	validate_benchmark_config: {
		active: 'Checking the benchmark plan',
		done: 'Checked the benchmark plan'
	},
	submit_benchmark: { active: 'Submitting the benchmark', done: 'Submitted the benchmark' },
	list_blend_models: { active: 'Checking blendable models', done: 'Checked blendable models' },
	get_blend_config: { active: 'Reading the blend plan', done: 'Read the blend plan' },
	update_blend_config: { active: 'Updating the blend plan', done: 'Updated the blend plan' },
	validate_blend_config: { active: 'Checking the blend plan', done: 'Checked the blend plan' },
	submit_blend: { active: 'Submitting the blend', done: 'Submitted the blend' },
	get_blend_results: { active: 'Reading blend results', done: 'Read blend results' },
	list_jobs: { active: 'Listing runs', done: 'Listed runs' },
	list_failed_jobs: { active: 'Checking failed runs', done: 'Checked failed runs' },
	get_job_info: { active: 'Reading run details', done: 'Read run details' },
	get_job_logs: { active: 'Reading run logs', done: 'Read run logs' },
	rerun_job: { active: 'Rerunning', done: 'Reran' },
	get_job_metrics: { active: 'Loading metrics', done: 'Loaded metrics' },
	get_skill_scores: { active: 'Loading skill scores', done: 'Loaded skill scores' },
	get_spatial_summary: {
		active: 'Loading the spatial summary',
		done: 'Loaded the spatial summary'
	},
	run_code_sandbox: { active: 'Running a computation', done: 'Ran a computation' },
	run_code: { active: 'Running custom analysis', done: 'Ran custom analysis' }
};

function fallbackLabel(name: string): ToolLabel {
	const words = name.replace(/_/g, ' ');
	const label = words.charAt(0).toUpperCase() + words.slice(1);
	return { active: label, done: label };
}

/** What a tool step is doing, or did, in the user's terms. */
export function toolLabel(toolCall: Pick<ChatToolCall, 'name' | 'status'>): string {
	if (toolCall.status === 'pending' && CODE_TOOLS.has(toolCall.name))
		return 'Writing analysis code';
	const label = TOOL_LABELS[toolCall.name] ?? fallbackLabel(toolCall.name);
	return toolCall.status === 'pending' || toolCall.status === 'running' ? label.active : label.done;
}

export function codeForToolCall(toolCall: ChatToolCall): string | null {
	if (!CODE_TOOLS.has(toolCall.name)) return null;
	const code = toolCall.input.code;
	return typeof code === 'string' && code.length > 0 ? code : null;
}

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
