import { describe, expect, it } from 'vitest';

import type { ChatMessage, ProvenanceStep } from '../src/lib/api';
import {
	analysisHeading,
	groupByTurn,
	questionFor,
	withAnalysisName,
	type Analysis
} from '../src/lib/workspace/analyses';
import { lineageRows, stepAttribution } from '../src/lib/workspace/lineage';
import { provenanceStep } from './fixtures/workspace';

describe('who decided a step', () => {
	it('names the model, ruleset and the approval for an approved assistant proposal', () => {
		const approvedAt = '2026-09-22T10:00:00Z';
		const text = stepAttribution(
			provenanceStep({
				model_name: 'claude-sonnet-4-6',
				ruleset_id: 'default',
				ruleset_version: 3,
				approval_id: 'a1',
				approved_at: approvedAt
			})
		);
		expect(text).toBe(
			`Proposed by claude-sonnet-4-6 under the default ruleset v3, approved by you at ${new Date(approvedAt).toLocaleString()}`
		);
	});

	it.each([
		['assistant', 'Run by the assistant'],
		['user', 'Started by you'],
		['system', 'Recorded by the platform']
	])('describes an unapproved %s step', (actor_kind, text) => {
		expect(stepAttribution(provenanceStep({ actor_kind }))).toBe(text);
	});

	const metricsFile = {
		id: 'f1',
		label: 'metrics',
		filename: 'metrics.nc',
		kind: 'metrics',
		format: 'netcdf',
		content_hash: 'c',
		provenance_step_id: 's1'
	};
	const recordOf = (made: ProvenanceStep) => ({
		artifact: metricsFile,
		artifacts: [metricsFile],
		producers: ['s1'],
		lineage: '',
		steps: [made]
	});

	it('says when a run made elsewhere was added by hand, beside who started it', () => {
		const addedAt = '2026-10-02T09:00:00Z';

		const [row] = lineageRows(
			recordOf(provenanceStep({ actor_kind: 'user', job_id: 'j1', added_at: addedAt }))
		);

		expect(row.attribution).toBe('Started by you');
		expect(row.added).toBe(`Added by you at ${new Date(addedAt).toLocaleString()}`);
	});

	it('says when it was the assistant that added the run', () => {
		const addedAt = '2026-10-02T09:00:00Z';
		const added = {
			...provenanceStep({ actor_kind: 'user', job_id: 'j1', added_at: addedAt }),
			added_by: 'assistant' as const
		};

		const [row] = lineageRows(recordOf(added));

		expect(row.added).toBe(`Added by the assistant at ${new Date(addedAt).toLocaleString()}`);
	});

	it('says nothing about adding for a run made in the workspace', () => {
		const [row] = lineageRows(recordOf(provenanceStep({ actor_kind: 'user', job_id: 'j1' })));

		expect(row.added).toBeNull();
	});
});

const transcript: ChatMessage[] = [
	{ id: 'u1', role: 'user', content: 'Compare AIFS and FuXi', created_at: '' },
	{ id: 't1', role: 'assistant', content: 'Submitted.', created_at: '' },
	{
		id: 'f1',
		role: 'user',
		content: 'Your runs finished; continue.',
		created_at: '',
		trigger: 'follow_up:x'
	},
	{ id: 't2', role: 'assistant', content: 'Here is the map.', created_at: '' }
];

describe('analyses', () => {
	it('names a turn by the question the user asked, skipping platform follow-ups', () => {
		expect(questionFor('t1', transcript)).toBe('Compare AIFS and FuXi');
		expect(questionFor('t2', transcript)).toBe('Compare AIFS and FuXi');
		expect(questionFor('missing', transcript)).toBeNull();
	});

	function analysisOf(turnId: string): Analysis {
		const [analysis] = groupByTurn([
			{
				id: 'fig',
				label: 'fig',
				title: 'fig',
				group: '',
				kind: 'figure',
				format: 'png',
				created_at: '2026-09-22T00:00:00Z',
				job_id: null,
				step_id: 's',
				turn_id: turnId,
				run_job_ids: [],
				breadcrumb: [],
				summary: {},
				supporting: false
			}
		]);
		return analysis;
	}

	it('goes by its name once it has one, keeping the question for hovering', () => {
		const names = [{ turn_id: 't2', title: 'AIFS vs FuXi map', named_by: 'assistant' as const }];
		expect(analysisHeading(analysisOf('t2'), transcript, names)).toEqual({
			text: 'AIFS vs FuXi map',
			question: 'Compare AIFS and FuXi'
		});
	});

	it('goes by the question until it is named', () => {
		expect(analysisHeading(analysisOf('t2'), transcript, []).text).toBe('Compare AIFS and FuXi');
	});

	it('falls back to a timestamp when the turn is not in the loaded chat', () => {
		expect(analysisHeading(analysisOf('gone'), transcript, []).text).toMatch(/^Analysis from /);
	});

	it('sets, replaces and clears one analysis name without touching the others', () => {
		const names = [
			{ turn_id: 't1', title: 'First', named_by: 'assistant' as const },
			{ turn_id: 't2', title: 'Second', named_by: 'assistant' as const }
		];
		const renamed = withAnalysisName(names, 't2', {
			turn_id: 't2',
			title: 'Mine',
			named_by: 'user'
		});
		expect(renamed.map((n) => [n.turn_id, n.title])).toEqual([
			['t1', 'First'],
			['t2', 'Mine']
		]);
		expect(withAnalysisName(renamed, 't2', null).map((n) => n.turn_id)).toEqual(['t1']);
	});
});
