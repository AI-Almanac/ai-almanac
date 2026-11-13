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
