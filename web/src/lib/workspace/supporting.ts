import type { WorkspaceArtifact } from '$lib/api';

export type FoldedFiles = {
	results: WorkspaceArtifact[];
	/** Files the results were made from, listed out of the way. */
	supporting: WorkspaceArtifact[];
};

/** Results first, supporting files apart; when nothing else is there, they are the results. */
export function foldSupporting(files: WorkspaceArtifact[]): FoldedFiles {
	const results = files.filter((file) => !file.supporting);
	if (results.length === 0) return { results: files, supporting: [] };
	return { results, supporting: files.filter((file) => file.supporting) };
}

export type AnalysisFileSort = FoldedFiles & {
	/** The code the assistant ran, oldest first. */
	scripts: WorkspaceArtifact[];
};

/** Titles a script is given when it is made; any other title is one a person chose. */
const MADE_SCRIPT_TITLES = new Set(['Script', 'Code that was run']);

export function isScript(file: WorkspaceArtifact): boolean {
	return file.supporting && file.format === 'python';
}

function oldestFirst(files: WorkspaceArtifact[]): WorkspaceArtifact[] {
	return [...files].sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** An analysis's results, the other files they were made from, and the code that ran. */
export function sortAnalysisFiles(files: WorkspaceArtifact[]): AnalysisFileSort {
	return {
		...foldSupporting(files.filter((file) => !isScript(file))),
		scripts: oldestFirst(files.filter(isScript))
	};
}

/**
 * Whether to list the results to choose between: only when there is a choice, or when
 * the file on show is not a result and the way back to one is needed.
 */
export function offersResultChoice(results: WorkspaceArtifact[], shownId: string): boolean {
	return results.length > 1 || !results.some((file) => file.id === shownId);
}

function madeBy(script: WorkspaceArtifact, files: WorkspaceArtifact[]): WorkspaceArtifact[] {
	if (script.step_id === null) return [];
	return files.filter((file) => file.step_id === script.step_id && !isScript(file));
}

function whatItMade(made: WorkspaceArtifact[]): string {
	if (made.length === 0) return 'made no files';
	const [first, ...rest] = made.map((file) => file.title || file.label);
	return rest.length === 0 ? `made ${first}` : `made ${first} and ${rest.length} more`;
}

/** A script goes by when it ran and what it made, e.g. "Script 2 · made Rain anomaly map". */
export function scriptName(script: WorkspaceArtifact, files: WorkspaceArtifact[]): string {
	const runOrder = oldestFirst(files.filter(isScript)).findIndex((file) => file.id === script.id);
	const numbered = runOrder < 0 ? 'Script' : `Script ${runOrder + 1}`;
	return `${numbered} · ${whatItMade(madeBy(script, files))}`;
}

/** What a file in an analysis is called; a script keeps a name someone gave it. */
export function analysisFileName(file: WorkspaceArtifact, files: WorkspaceArtifact[]): string {
	if (isScript(file) && MADE_SCRIPT_TITLES.has(file.title)) return scriptName(file, files);
	return file.title || file.label;
}
