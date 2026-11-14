import { describe, expect, it } from 'vitest';

import type { ChatArtifact, ChatEvent, ChatMessage } from '../src/lib/api';
import { resultTable } from '../src/lib/chat/tools';
import { artifactRefFromHref, renderMarkdown } from '../src/lib/chat/markdown';
import { emptyAssistantTurn, foldTurnEvent } from '../src/lib/chat/turn';
import { allJobsComplete, submittedJobs } from '../src/lib/chat/tool-results';
import { NOT_APPROVED } from '../src/lib/chat/approval';
import {
	groupState,
	groupSummary,
	liveLabel,
	stepState,
	turnTimeline,
	type ActivityGroup
} from '../src/lib/chat/timeline';

const T = 'turn-1';

function stream(events: ChatEvent[]): ChatMessage {
	return events.reduce(foldTurnEvent, emptyAssistantTurn(T));
}

function toolCall(
	id: string,
	name: string,
	status: 'pending' | 'running' | 'completed' | 'failed'
): ChatEvent {
	return {
		type: 'tool_call',
		turn_id: T,
		tool_call: { id, name, status, input: {}, artifacts: [] }
	};
}

function toolResult(id: string): ChatEvent {
	return { type: 'tool_result', turn_id: T, tool_call_id: id, status: 'completed', result: {} };
}

const figure: ChatArtifact = {
	id: 'fig-1',
	kind: 'figure',
	url: '/chat/figures/fig-1/public',
	label: 'FuXi FAR histogram',
	created_at: '2026-10-08T00:00:00Z'
};

describe('chat turn timeline', () => {
	it('shows reply text and tool work in the order they happened', () => {
		const turn = stream([
			{ type: 'text_delta', turn_id: T, content: 'Let me look.' },
			toolCall('c1', 'get_job_metrics', 'running'),
			toolResult('c1'),
			{ type: 'text_delta', turn_id: T, content: 'FuXi leads.' }
		]);

		expect(turnTimeline(turn).map((item) => item.kind)).toEqual(['text', 'activity', 'text']);
	});

	it('starts a new paragraph in the turn text when the reply resumes after tool work', () => {
		const turn = stream([
			{ type: 'text_delta', turn_id: T, content: 'Let me look.' },
			toolCall('c1', 'get_job_metrics', 'running'),
			{ type: 'text_delta', turn_id: T, content: 'FuXi leads.' }
		]);

		expect(turn.content).toBe('Let me look.\n\nFuXi leads.');
	});

	it('collapses back-to-back analyses and their progress notes into one group', () => {
		const turn = stream([
			toolCall('c1', 'run_code', 'running'),
			toolResult('c1'),
			{ type: 'thinking_delta', turn_id: T, content: 'Now the second model.' },
			toolCall('c2', 'run_code', 'running'),
			toolResult('c2'),
			toolCall('c3', 'get_job_metrics', 'running'),
			toolResult('c3')
		]);

		const items = turnTimeline(turn);
		expect(items).toHaveLength(1);
		expect(groupSummary(items[0] as ActivityGroup)).toBe('Ran custom analysis ×2 · Loaded metrics');
	});

	it('names the step in progress, including a code tool whose code is still being written', () => {
		const writing = turnTimeline(stream([toolCall('c1', 'run_code', 'pending')]));
		expect(liveLabel(writing[0] as ActivityGroup)).toBe('Writing analysis code');

		const running = turnTimeline(
			stream([toolCall('c1', 'run_code', 'pending'), toolCall('c1', 'run_code', 'running')])
		);
		expect(liveLabel(running[0] as ActivityGroup)).toBe('Running custom analysis');
		expect((running[0] as ActivityGroup).steps).toHaveLength(1);
	});

	it('reports a step waiting on the user as waiting, not as done', () => {
		const turn = stream([toolCall('c1', 'submit_benchmark', 'running')]);
		const waiting: ChatMessage = {
			...turn,
			tool_calls: turn.tool_calls?.map((call) => ({ ...call, awaiting_approval: true }))
		};
		const group = turnTimeline(waiting)[0] as ActivityGroup;

		expect(stepState(group.steps[0], false)).toBe('waiting');
		expect(groupState(group, false)).toBe('waiting');
		expect(groupSummary(group)).toBe('Waiting for your approval');
	});

	it('reports a step the user declined as declined, apart from one that failed', () => {
		const settled = (name: string, result: unknown) =>
			turnTimeline(
				stream([
					toolCall('c1', name, 'running'),
					{ type: 'tool_result', turn_id: T, tool_call_id: 'c1', status: 'failed', result }
				])
			)[0] as ActivityGroup;

		expect(groupState(settled('submit_benchmark', NOT_APPROVED), false)).toBe('declined');
		expect(groupState(settled('plot_map', { error: 'No such file' }), false)).toBe('failed');
	});

	it('reports work under way while the turn is live, and done once it settles', () => {
		const group = turnTimeline(stream([toolCall('c1', 'run_code', 'running')]))[0] as ActivityGroup;

		expect(groupState(group, true)).toBe('active');
		expect(groupState(group, false)).toBe('done');
	});

	it('carries figures a tool produced on the group so they render inline', () => {
		const turn = stream([
			toolCall('c1', 'run_code', 'running'),
			{ type: 'artifact', turn_id: T, tool_call_id: 'c1', artifact: figure },
			toolResult('c1')
		]);

		expect((turnTimeline(turn)[0] as ActivityGroup).figures.map((f) => f.id)).toEqual(['fig-1']);
	});

	it('renders turns saved before blocks existed as text followed by their tools', () => {
		const legacy: ChatMessage = {
			id: 'old',
			role: 'assistant',
			content: 'Summary.',
			created_at: '2026-10-01T00:00:00Z',
			tool_calls: [{ id: 'c1', name: 'run_code', status: 'completed', input: {}, artifacts: [] }]
		};

		expect(turnTimeline(legacy).map((item) => item.kind)).toEqual(['text', 'activity']);
	});

	it('shows a reply after its steps when the turn was saved with the steps alone', () => {
		const saved: ChatMessage = {
			id: 'lost',
			role: 'assistant',
			content: 'I have set up the form.',
			created_at: '2026-11-11T00:00:00Z',
			tool_calls: [{ id: 'c1', name: 'open_panel', status: 'completed', input: {}, artifacts: [] }],
			blocks: [{ kind: 'tool', tool_call_id: 'c1' }]
		};

		const timeline = turnTimeline(saved);

		expect(timeline.map((item) => item.kind)).toEqual(['activity', 'text']);
		expect(timeline.at(-1)).toMatchObject({ text: 'I have set up the form.' });
	});
});

describe('tool results', () => {
	const jobs = {
		run_id: 'run-1',
		jobs: [
			{ id: 'job-1', model_name: 'fuxi', model_display_name: 'FuXi', status: 'queued' },
			{ id: 'job-2', model_name: 'climatology', status: 'queued' }
		]
	};

	it('lists submitted runs by model name', () => {
		expect(submittedJobs(jobs).map((job) => job.label)).toEqual([
			'FuXi',
			'Traditional Climatology'
		]);
		expect(submittedJobs({ error: 'nope' })).toEqual([]);
	});

	it('treats a submission as finished only when every run is complete', () => {
		const submitted = submittedJobs(jobs);
		expect(allJobsComplete(submitted, { 'job-1': 'complete' })).toBe(false);
		expect(allJobsComplete(submitted, { 'job-1': 'complete', 'job-2': 'complete' })).toBe(true);
	});

	it('tabulates the headline spatial metrics', () => {
		const table = resultTable('get_job_metrics', {
			job_id: 'job-1',
			windows: [
				{
					window: '1-15',
					model: 'fuxi',
					tolerance_days: 3,
					metrics: {
						false_alarm_rate: { mean: 0.25, unit: 'fraction' },
						mae: { mean: 4.5, unit: 'days' },
						acc: { mean: 0.1, unit: 'dimensionless' }
					}
				}
			]
		});

		expect(table?.columns).toEqual([
			'Model',
			'Forecast days',
			'False alarms',
			'Mean absolute error'
		]);
		expect(table?.rows).toEqual([['FuXi', 'Days 1–15', '25.0%', '4.5 d']]);
	});

	it('tabulates skill scores and blend skill', () => {
		const skill = resultTable('get_skill_scores', {
			windows: [
				{
					model: 'aifs',
					window: '1-30',
					overall: { ranked_probability_skill_score: 0.12, brier_skill_score: -0.05, auc: 0.7 },
					bins: []
				}
			]
		});
		expect(skill?.rows).toEqual([['AIFS', 'Days 1–30', '0.120', '-0.050', '0.700']]);

		const blend = resultTable('get_blend_results', {
			skill: [
				{ model: 'blended_model', is_blend: true, rps_skill: 0.2, brier_skill: null, auc: 0.65 }
			]
		});
		expect(blend?.rows).toEqual([['Week-level blend', '0.200', '—', '0.650']]);
	});

	it('shows no table for errors or unknown tools', () => {
		expect(resultTable('get_job_metrics', { error: 'Job is not complete' })).toBeNull();
		expect(resultTable('list_models', { models: [] })).toBeNull();
	});
});

describe('artifact links', () => {
	it('renders run links as buttons that carry the job id', () => {
		const html = renderMarkdown('[Open the skill maps](run:job-7)', { openArtifactLinks: true });
		const doc = new DOMParser().parseFromString(html, 'text/html');
		const button = doc.querySelector('button[data-run-ref]');

		expect(button?.getAttribute('data-run-ref')).toBe('job-7');
		expect(button?.textContent).toBe('Open the skill maps');
		expect(doc.querySelector('a')).toBeNull();
	});

	it('recognises only artifact links', () => {
		expect(artifactRefFromHref('artifact:onset-map')).toBe('onset-map');
		expect(artifactRefFromHref('https://example.com')).toBeNull();
		expect(artifactRefFromHref('artifact:')).toBeNull();
	});

	it('renders artifact links as buttons that carry the label', () => {
		const html = renderMarkdown('See [the map](artifact:onset-map).', { openArtifactLinks: true });
		const doc = new DOMParser().parseFromString(html, 'text/html');
		const button = doc.querySelector('button[data-artifact-ref]');

		expect(button?.getAttribute('data-artifact-ref')).toBe('onset-map');
		expect(button?.textContent).toBe('the map');
		expect(doc.querySelector('a')).toBeNull();
	});

	it('renders artifact links as plain text when they cannot be opened', () => {
		const html = renderMarkdown('See [the map](artifact:onset-map).');
		const doc = new DOMParser().parseFromString(html, 'text/html');

		expect(doc.body.textContent?.trim()).toBe('See the map.');
		expect(doc.querySelector('button, a')).toBeNull();
	});

	it('cannot be used to inject markup', () => {
		const label = 'a"onmouseover="alert(1)"<b>';
		const html = renderMarkdown(`[x](artifact:${label})`, { openArtifactLinks: true });
		const doc = new DOMParser().parseFromString(html, 'text/html');

		expect(doc.querySelector('[onmouseover], b')).toBeNull();
		expect(doc.querySelector('[data-artifact-ref]')?.getAttribute('data-artifact-ref')).toBe(label);
	});

	it('leaves ordinary links alone', () => {
		const html = renderMarkdown('[docs](https://example.com)', { openArtifactLinks: true });
		expect(html).toContain('href="https://example.com"');
	});
});
