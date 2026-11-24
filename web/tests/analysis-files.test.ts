import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { WorkspaceArtifact } from '../src/lib/api';
import AnalysisFiles from '../src/lib/components/workspace/AnalysisFiles.svelte';
import {
	analysisFileName,
	offersResultChoice,
	sortAnalysisFiles
} from '../src/lib/workspace/supporting';
import { workspaceArtifact } from './fixtures/workspace';

function script(id: string, step: string, at: string, title = 'Code that was run') {
	return workspaceArtifact({
		id,
		title,
		kind: 'document',
		format: 'python',
		supporting: true,
		step_id: step,
		created_at: at
	});
}

function figure(id: string, title: string, step: string | null = null) {
	return workspaceArtifact({ id, title, step_id: step, created_at: '2026-11-24T08:44:00Z' });
}

const first = script('code-1', 'step-1', '2026-11-24T08:40:00Z');
const second = script('code-2', 'step-2', '2026-11-24T08:42:00Z', 'Script');
const third = script('code-3', 'step-3', '2026-11-24T08:44:00Z');
const onsetChange = figure('fig', 'Onset error change, 15 mm vs 20 mm', 'step-3');
const outlines = workspaceArtifact({ id: 'outlines', title: 'Area outlines', supporting: true });
// Listed newest first, as the workspace sends them.
const files = [onsetChange, third, second, first];

describe('naming the code an analysis ran', () => {
	it('numbers scripts in the order they ran and says what each made', () => {
		expect(analysisFileName(first, files)).toBe('Script 1 · made no files');
		expect(analysisFileName(second, files)).toBe('Script 2 · made no files');
		expect(analysisFileName(third, files)).toBe(
			'Script 3 · made Onset error change, 15 mm vs 20 mm'
		);
	});

	it('counts the rest when a script made several files', () => {
		const map = figure('map', 'Rain anomaly map', 'step-3');
		expect(analysisFileName(third, [...files, map])).toBe(
			'Script 3 · made Onset error change, 15 mm vs 20 mm and 1 more'
		);
	});

	it('keeps a name someone gave a script', () => {
		const renamed = { ...first, title: 'Threshold sweep' };
		expect(analysisFileName(renamed, [renamed])).toBe('Threshold sweep');
	});

	it('leaves other files their own titles', () => {
		expect(analysisFileName(onsetChange, files)).toBe(onsetChange.title);
	});
});

describe('sorting an analysis’s files', () => {
	it('keeps scripts apart from results and other supporting files, oldest script first', () => {
		const sorted = sortAnalysisFiles([...files, outlines]);
		expect(sorted.results.map((file) => file.id)).toEqual(['fig']);
		expect(sorted.supporting.map((file) => file.id)).toEqual(['outlines']);
		expect(sorted.scripts.map((file) => file.id)).toEqual(['code-1', 'code-2', 'code-3']);
	});

	it('offers results to choose between only when there is a choice or a way back is needed', () => {
		const other = figure('fig-2', 'Second figure');
		expect(offersResultChoice([onsetChange], 'fig')).toBe(false);
		expect(offersResultChoice([onsetChange, other], 'fig')).toBe(true);
		expect(offersResultChoice([onsetChange], 'code-1')).toBe(true);
	});
});

describe('the files row on the stage', () => {
	const analysis = (list: WorkspaceArtifact[]) => ({
		key: 'turn-1',
		turnId: 'turn-1',
		files: list,
		startedAt: '2026-11-24T08:40:00Z'
	});

	it('lists no result when the one on show is the only one, but still offers its code', async () => {
		const onOpen = vi.fn();
		render(AnalysisFiles, { analysis: analysis(files), shownId: 'fig', onOpen });

		const row = within(screen.getByRole('navigation', { name: 'Files in this analysis' }));
		expect(row.queryByRole('button', { name: onsetChange.title })).toBeNull();
		await fireEvent.click(row.getByText('Code (3)'));
		await fireEvent.click(row.getByRole('button', { name: /^Script 3 · made Onset/ }));
		expect(onOpen).toHaveBeenCalledWith(third);
	});

	it('offers the way back to the result while a script is on show', () => {
		render(AnalysisFiles, { analysis: analysis(files), shownId: 'code-1', onOpen: () => {} });

		expect(screen.getByRole('button', { name: onsetChange.title })).toBeTruthy();
	});
});
